"""盘中巡检：每一轮跟进全部未结束的订单、按订单同步预留、对齐持仓的止损单，发现无保护仓位时告警，
最后在到期时做收盘对账。

见 session-patrol spec、close-reconciliation spec「巡检里的收盘对账」和 add-session-patrol 的
design。执行模块和风控里会改变状态的调用都拿着全程序共用的那把锁，一项一项地拿；推送告警时不拿锁。
"""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.modules.execution import (
    BrokerUnavailableError,
    ExecutionService,
    Order,
    Protection,
    StopOrder,
    StopStatus,
)
from tradingagentspro.modules.notification import NotificationService
from tradingagentspro.modules.risk import RiskService
from tradingagentspro.workflows.names import ORDER_STATUSES, SIDES, STOP_STATUSES
from tradingagentspro.workflows.reconciliation import close_reconcile_if_due

_log = logging.getLogger(__name__)


class PatrolConfig(BaseModel):
    """盘中巡检的配置和默认值，含义见 session-patrol spec「巡检配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 一轮做完以后等这么久再做下一轮
    interval: timedelta = Field(default=timedelta(seconds=15), gt=timedelta(0))


class SessionPatrol:
    """盘中巡检，见 session-patrol spec。

    run_round 做一轮，可以直接调用；start 在守护线程里循环，stop 停下并等线程结束。
    lock 是全程序共用的那把锁，见 spec「执行模块和风控的调用串行」。
    """

    def __init__(
        self,
        execution: ExecutionService,
        risk: RiskService,
        notification: NotificationService,
        lock: threading.RLock,
        config: PatrolConfig,
    ) -> None:
        self._execution = execution
        self._risk = risk
        self._notification = notification
        self._lock = lock
        self._config = config
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        # 告警过的无保护仓位：股票到那一段无保护的开始时刻，见 design D8
        self._alerted: dict[str, datetime] = {}
        # 上次看到的止损单：股票到（客户端订单 ID、状态、股数、止损价），见 design D9
        self._stops: dict[str, tuple[str, StopStatus, int, Decimal]] = {}

    def start(self) -> None:
        """开一个守护线程 session-patrol：立即做第一轮，之后每一轮做完等一个间隔，见 design D11。

        开始时写一条 INFO：盘中巡检已开始，间隔 15 秒。
        """
        seconds = self._config.interval.total_seconds()
        self._thread = threading.Thread(target=self._loop, name="session-patrol", daemon=True)
        self._thread.start()
        _log.info("盘中巡检已开始，间隔 %g 秒", seconds)

    def stop(self) -> None:
        """不再开始新的一项和新的一轮，等线程结束；没有开始过时什么都不做。

        正在做的那一项做完才结束；每一项的券商调用都有超时，所以不设等待的上限。
        """
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        """做一轮，等一个间隔，直到停止；一轮里出了意外的异常也照常做下一轮。"""
        seconds = self._config.interval.total_seconds()
        while not self._stopping.is_set():
            try:
                self.run_round()
            except Exception:
                _log.exception("盘中巡检：这一轮出错")
            self._stopping.wait(seconds)

    def run_round(self) -> None:
        """做一轮：跟进订单、同步预留、对齐止损单，见 session-patrol spec「每一轮」；最后是收盘
        对账，见 close-reconciliation spec「巡检里的收盘对账」。

        停止以后不再开始新的一项，直接返回。
        """
        tracked = self._track_orders()
        self._sync_reservations()
        self._align_stops(tracked)
        self._close_reconcile()

    def _track_orders(self) -> set[str]:
        """第 1 步：每一笔未结束的订单按创建的先后各跟进一次，有变化时写日志。

        返回跟进没有出错的订单的股票：跟进时已经对齐过，第 3 步不再对齐，见 design D4。
        """
        tracked: set[str] = set()
        if self._stopping.is_set():
            return tracked
        orders: tuple[Order, ...] = ()
        with self._item("列出未结束的订单"):
            orders = self._execution.open_orders()
        for before in orders:
            if self._stopping.is_set():
                break
            with self._item(f"跟进 {before.symbol} 的订单 {before.plan_id}"):
                with self._lock:
                    after = self._execution.track(before.plan_id)
                tracked.add(after.symbol)
                _log_order(before, after)
        return tracked

    def _sync_reservations(self) -> None:
        """第 2 步：有有效预留、已经有订单的方案，上报累计成交；订单到了终态就释放。"""
        if self._stopping.is_set():
            return
        plan_ids: tuple[UUID, ...] = ()
        with self._item("列出有效预留"):
            plan_ids = self._risk.active_plan_ids()
        for plan_id in plan_ids:
            if self._stopping.is_set():
                return
            with self._item(f"同步方案 {plan_id} 的预留"), self._lock:
                self._sync(plan_id)

    def _sync(self, plan_id: UUID) -> None:
        order = self._execution.order(plan_id)
        if order is None:
            return
        self._risk.record_fill(plan_id, order.filled)
        if order.status.is_terminal:
            self._risk.release(plan_id)

    def _align_stops(self, tracked: set[str]) -> None:
        """第 3 步：要对齐的股票按代码顺序各处理一次，然后写止损单的日志、按无保护仓位告警。

        第 1 步跟进过的股票（tracked）只读止损保护，不再对齐，见 design D4；其余的对齐一次。
        读券商全部持仓失败时写 WARNING，这一轮不做这一步，见 design D5。
        """
        if self._stopping.is_set():
            return
        symbols: tuple[str, ...] | None = None
        with self._item("列出要对齐止损单的股票"):
            try:
                symbols = self._execution.symbols_to_protect()
            except BrokerUnavailableError as error:
                _log.warning("盘中巡检：读取券商持仓失败（%s），这一轮没有对齐止损单", error)
        if symbols is None:
            return  # 没有列出来：这一步跳过，告警记录也不动
        for symbol in symbols:
            if self._stopping.is_set():
                return
            with self._item(f"对齐 {symbol} 的止损单"):
                with self._lock:
                    if symbol in tracked:
                        protection = self._execution.protection(symbol)
                    else:
                        protection = self._execution.protect(symbol)
                self._log_stop(symbol, protection)
                self._alert(symbol, protection)
        self._forget(set(symbols))

    def _close_reconcile(self) -> None:
        """最后一项：收盘对账到期就做，见 add-close-reconciliation 的 design D2。

        放在对齐止损单之后：同一轮刚跟进、对齐过，收盘对账补上的才算差异。
        """
        if self._stopping.is_set():
            return
        with self._item("收盘对账"):
            close_reconcile_if_due(self._execution, self._notification, self._lock)

    def _log_stop(self, symbol: str, protection: Protection | None) -> None:
        """当前止损单和上次看到的不同时写一条 INFO，第一次看到的也写，见 design D9。"""
        stop = protection.stop if protection else None
        if stop is None:
            self._stops.pop(symbol, None)
            return
        seen = (stop.client_order_id, stop.status, stop.quantity, stop.stop_price)
        if self._stops.get(symbol) == seen:
            return
        self._stops[symbol] = seen
        _log.info("止损单 %s", _describe_stop(stop))

    def _alert(self, symbol: str, protection: Protection | None) -> None:
        """无保护仓位：同一段只告警一次；告警过的解除时写 INFO，见 design D8。不拿锁。"""
        since = protection.unprotected_since if protection else None
        if since is None:
            if self._alerted.pop(symbol, None) is not None:
                _log.info("%s 不再是无保护仓位", symbol)
            return
        if self._alerted.get(symbol) == since:
            return
        self._alerted[symbol] = since
        self._notification.alert(unprotected_text(symbol, since))

    def _forget(self, symbols: set[str]) -> None:
        """不再需要对齐的股票：删掉告警记录（写 INFO）和上次看到的止损单。"""
        for symbol in [s for s in self._alerted if s not in symbols]:
            del self._alerted[symbol]
            _log.info("%s 不再是无保护仓位", symbol)
        for symbol in [s for s in self._stops if s not in symbols]:
            del self._stops[symbol]

    @contextmanager
    def _item(self, name: str) -> Iterator[None]:
        """一项：出错写 ERROR（带异常栈和这一项的名字），不往外抛，见 design D10。

        列出一步要处理的东西时也用它：出错时这一步没有要处理的，跳过。
        """
        try:
            yield
        except Exception:
            _log.exception("盘中巡检：%s 出错", name)


def unprotected_text(symbol: str, since: datetime) -> str:
    """无保护仓位的告警文字，见 session-patrol spec「无保护仓位的告警」。"""
    return (
        f"{symbol} 是无保护仓位（从 {since.astimezone(UTC):%Y-%m-%d %H:%M} UTC 起）："
        "止损单挂单或改单被券商拒绝。巡检每一轮都会重试补挂，补挂成功之前不能加仓"
    )


def _describe_stop(stop: StopOrder) -> str:
    """止损单的日志内容：股票、客户端订单 ID、状态、股数、止损价。"""
    return (
        f"{stop.symbol} {stop.client_order_id}：{STOP_STATUSES[stop.status]}，"
        f"{stop.quantity} 股，止损价 {stop.stop_price}"
    )


def _log_order(before: Order, after: Order) -> None:
    """跟进前后状态或累计成交不同时写一条 INFO，见 spec「订单和止损单变化的日志」。"""
    if (before.status, before.filled) == (after.status, after.filled):
        return
    _log.info(
        "订单 %s %s %s：%s → %s，累计成交 %d / %d",
        after.symbol,
        SIDES[after.side],
        after.plan_id,
        ORDER_STATUSES[before.status],
        ORDER_STATUSES[after.status],
        after.filled,
        after.quantity,
    )
