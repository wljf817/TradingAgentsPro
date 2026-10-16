"""对账的流程：盘中巡检每一轮最后的收盘对账，以及收盘对账结果、对账条目和启动对账告警的文字。

见 close-reconciliation spec、bootstrap spec「启动对账」和 add-close-reconciliation 的 design D2、
D8、D9。收盘对账拿着全程序共用的那把锁做，推送结果时不拿锁。对账的结果和对账跟进过的订单的变化
写进程序日志，见 program-log spec「执行模块的动作」和 add-program-log 的 design D5。
"""

import logging
import threading
from datetime import UTC

from tradingagentspro.modules.execution import (
    BrokerUnavailableError,
    CloseReconciliation,
    ExecutionService,
    Reconciliation,
)
from tradingagentspro.modules.notification import NotificationService
from tradingagentspro.modules.programlog import ACCOUNT, Action, Actor, ProgramLog
from tradingagentspro.workflows.entries import order_changes
from tradingagentspro.workflows.names import ORDER_STATUSES, SIDES

_log = logging.getLogger(__name__)

# 有不一致时告警的最后一行；启动对账、收盘对账和降级恢复后的对账共用
PAUSED = "已暂停开仓，之后的对账没有不一致时恢复"
_RESUMED = "没有不一致，已恢复开仓"


def close_reconcile_if_due(
    execution: ExecutionService,
    notification: NotificationService,
    lock: threading.RLock,
    log: ProgramLog,
) -> None:
    """收盘对账到期就做，见 close-reconciliation spec「巡检里的收盘对账」。

    判断是否到期不拿锁；对账拿着锁，跟进过的订单有变化的写订单变化；放开锁以后写程序日志的
    对账（输出的引用是交易日，说明是结果消息），再推送结果。券商不可用时写 WARNING，下一轮再做；
    别的错误原样抛出，由巡检写 ERROR。
    """
    day = execution.close_due()
    if day is None:
        return
    try:
        with lock, order_changes(execution, log):
            record = execution.close_reconcile(day)
    except BrokerUnavailableError as error:
        _log.warning("收盘对账 %s 没做成：券商不可用（%s），下一轮再做", day, error)
        return
    text = close_text(record)
    log.write(Actor.EXECUTION, Action.RECONCILE, ACCOUNT, text, outputs=[day.isoformat()])
    notification.alert(ACCOUNT, text)


def close_text(record: CloseReconciliation) -> str:
    """收盘对账的结果消息，见 close-reconciliation spec「结果消息」。"""
    day = record.day.isoformat()
    if not record.clean:
        lines = [f"收盘对账 {day}：有差异，连续天数清零", *_difference_lines(record)]
    elif record.holdings > 0:
        lines = [
            f"收盘对账 {day}：没有差异，持仓 {record.holdings} 只，"
            f"连续 {record.streak} 个交易日无差异"
        ]
    else:
        lines = [f"收盘对账 {day}：没有差异，收盘时没有持仓，连续天数清零"]
    if not record.result.clean:
        lines.append(PAUSED)
    elif record.resumed:
        lines.append(_RESUMED)
    return "\n".join(lines)


def startup_text(result: Reconciliation) -> str:
    """启动对账有不一致时的告警，见 bootstrap spec「启动对账」。"""
    return "\n".join(["启动对账有不一致：", *inconsistency_lines(result), PAUSED])


def reconciliation_text(kind: str, result: Reconciliation) -> str:
    """程序日志的对账的说明：哪一种对账，没有不一致，或者每一条不一致一行。"""
    if result.clean:
        return f"{kind}：没有不一致"
    return "\n".join([f"{kind}有不一致：", *inconsistency_lines(result)])


def inconsistency_lines(result: Reconciliation) -> list[str]:
    """三类不一致，每条一行；收盘对账的结果和启动对账的告警共用。"""
    lines = [
        f"- 不认得的挂单：{w.symbol}，客户端订单 ID {w.client_order_id}，"
        f"券商订单号 {w.broker_order_id}"
        for w in result.stray_orders
    ]
    for symbol in sorted({s.symbol for s in result.extra_stops}):
        stops = "、".join(
            f"{s.client_order_id}（{s.broker_order_id}）"
            for s in result.extra_stops
            if s.symbol == symbol
        )
        lines.append(f"- 多出来的止损单：{symbol}，{stops}")
    lines += [f"- 来历不明的持仓：{symbol} {shares} 股" for symbol, shares in result.unexplained]
    return lines


def _difference_lines(record: CloseReconciliation) -> list[str]:
    """六类差异，每条一行，见 close-reconciliation spec「差异」。"""
    lines = inconsistency_lines(record.result)
    lines += [
        f"- 收盘时没结束的订单：{o.symbol} {SIDES[o.side]}，方案 {o.plan_id}，"
        f"对账以后是{ORDER_STATUSES[o.status]}"
        for o in record.leftover_orders
    ]
    lines += [
        f"- 对账补挂或重挂的止损单：{s.symbol}，{s.client_order_id}" for s in record.new_stops
    ]
    lines += [
        f"- 当天出现过的无保护仓位：{u.symbol}，从 {u.since.astimezone(UTC):%Y-%m-%d %H:%M} UTC 起"
        for u in record.unprotected
    ]
    return lines
