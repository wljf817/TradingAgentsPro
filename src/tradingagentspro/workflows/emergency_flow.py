"""紧急退出流程：盘中巡检每一轮里判定紧急风险退出和本地备用止损，撤单、作废还没下单的方案、清仓，
熔断时撤掉全部买单，执行后告警。

见 emergency-exit-flow spec 和 add-emergency-exit-flow 的 design。一轮里的每一项由巡检编排
（patrol.py 的 _emergency_exit）：judge 不拿锁；cancel_buys、advance 各拿一次全程序共用的那把锁；
report 只在释放预留时拿锁；推送告警都在锁外。
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.emergency import (
    EmergencyConfig,
    ExitReason,
    Holding,
    close_plan,
    reference_price,
)
from tradingagentspro.modules.execution import (
    Account,
    BrokerUnavailableError,
    HeldPosition,
    Order,
    OrderSide,
    OrderStatus,
)
from tradingagentspro.modules.marketdata import Quote, QuoteUnavailableError
from tradingagentspro.modules.notification import NotificationService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows.account_snapshot import account_snapshot
from tradingagentspro.workflows.names import ACTIONS, ORDER_STATUSES, SIDES
from tradingagentspro.workflows.order_flow import TradeServices

_log = logging.getLogger(__name__)

ZERO = Decimal(0)


class ExitCause(StrEnum):
    """为什么清仓：紧急退出的两种原因，加上本地备用止损。"""

    BREAKER = "breaker"  # 组合熔断
    STOCK_DROP = "stock_drop"  # 个股跌幅
    BACKUP_STOP = "backup_stop"  # 本地备用止损


_CAUSES = {
    ExitReason.DAILY_LOSS_BREAKER: ExitCause.BREAKER,
    ExitReason.STOCK_DROP: ExitCause.STOCK_DROP,
}


@dataclass(frozen=True, slots=True)
class Trigger:
    """一只股票这一轮触发清仓：当天的清仓方案、原因和告警里写的原因说明。"""

    plan: TradePlan
    cause: ExitCause
    detail: str


@dataclass(frozen=True, slots=True)
class Breaker:
    """这一轮熔断已触发：告警用的权益。"""

    equity: Decimal
    previous_close_equity: Decimal


@dataclass(frozen=True, slots=True)
class Judgement:
    """一轮的判定：交易日、风控用的账户数据、熔断，以及这一轮要推进的清仓方案（按代码排序）。"""

    day: date
    snapshot: AccountSnapshot
    breaker: Breaker | None
    exits: tuple[TradePlan, ...]


class StepKind(StrEnum):
    """一只股票这一项做到了哪里，见 design D5。"""

    HAS_ORDER = "has_order"  # 当天的清仓方案已经有订单，什么都没做
    WAITING = "waiting"  # 这只股票还有挂着的订单，等它们结束
    SUBMITTED = "submitted"  # 建立了清仓单
    NO_SHARES = "no_shares"  # 风控拒绝：没有可卖的股数，当天的紧急退出结束


@dataclass(frozen=True, slots=True)
class ExitStep:
    """一只股票这一项的结果：作废的方案、这一次撤掉的订单、在等的订单、建立的清仓单。"""

    kind: StepKind
    voided: tuple[TradePlan, ...] = ()
    cancelled: tuple[Order, ...] = ()
    waiting: tuple[Order, ...] = ()
    order: Order | None = None


@dataclass
class _Record:
    """这次运行里当天开始的一只股票的紧急退出，见 design D2。

    trigger 是第一次触发的原因；重启以后按清仓单找回来的为 None。announced：开始的告警已经推送过
    （找回来的一开始就是）。ended：当天的紧急退出已经结束、结果已经告警，不再处理。
    """

    plan: TradePlan
    day: date
    trigger: Trigger | None
    announced: bool
    ended: bool = False


def holdings_of(account: Account, quotes: Mapping[str, Quote]) -> tuple[Holding, ...]:
    """紧急退出判定用的持仓：账户数据的每只持仓加上行情；没有行情时两个价格为空。"""
    holdings = []
    for position in account.positions:
        quote = quotes.get(position.symbol)
        holdings.append(
            Holding(
                symbol=position.symbol,
                sector=position.sector,
                emergency_threshold=position.emergency_threshold,
                previous_close=None if quote is None else quote.previous_close,
                last_price=None if quote is None else quote.last_price,
                entry_price_today=position.entry_price_today,
            )
        )
    return tuple(holdings)


class EmergencyFlow:
    """盘中巡检每一轮里的紧急退出，见 emergency-exit-flow spec。

    judge 不拿锁；cancel_buys、advance 各拿一次锁，告警在锁外；report 只在释放预留时拿锁。
    只在巡检的线程里调用。
    """

    def __init__(
        self,
        services: TradeServices,
        notification: NotificationService,
        clock: Clock,
        calendar: TradingCalendar,
        config: EmergencyConfig,
    ) -> None:
        self._services = services
        self._notification = notification
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._records: dict[UUID, _Record] = {}  # 当天开始的紧急退出，按清仓方案 ID
        self._breaker_day: date | None = None  # 推送过熔断告警的交易日

    def judge(self) -> Judgement | None:
        """判定这一轮，见 spec「判定的输入」「要清仓的股票」和 design D3。

        盘外、读券商账户失败时返回 None：这一轮不判定。
        """
        session = self._calendar.session_at(self._clock.now())
        if session is None:
            return None
        execution = self._services.execution
        try:
            account = execution.account()
        except BrokerUnavailableError as error:
            _log.warning("紧急退出：读取券商账户失败（%s），这一轮不判定", error)
            return None
        quotes = self._quotes(account)
        day = session.day
        self._forget_before(day)
        holdings = {h.symbol: h for h in holdings_of(account, quotes)}
        emergency = self._services.emergency
        exits = emergency.check(holdings.values(), account.equity, account.previous_close_equity)
        triggers = {
            e.plan.symbol: Trigger(
                e.plan, _CAUSES[e.reason], _cause_text(_CAUSES[e.reason], holdings[e.plan.symbol])
            )
            for e in exits
        }
        for position in account.positions:
            if position.symbol not in triggers:
                trigger = self._backup_stop(position, holdings[position.symbol], day)
                if trigger is not None:
                    triggers[position.symbol] = trigger
        self._remember(triggers.values(), account, day)
        breaker = None
        if emergency.breaker_tripped():
            breaker = Breaker(account.equity, account.previous_close_equity)
        return Judgement(
            day=day,
            snapshot=account_snapshot(account, quotes),
            breaker=breaker,
            exits=self._pending(day),
        )

    def cancel_buys(self, judgement: Judgement) -> None:
        """熔断已触发：拿锁，有还没撤的买单就撤掉全部买单；每个交易日第一次时推送熔断告警。

        见 spec「单日亏损熔断」和 design D7。
        """
        breaker = judgement.breaker
        assert breaker is not None  # 只在熔断已触发时调用
        execution = self._services.execution
        with self._services.lock:
            buys = tuple(
                o
                for o in execution.open_orders()
                if o.side is OrderSide.BUY and not o.cancel_wanted
            )
            if buys:
                execution.cancel_buy_orders()
        for order in buys:
            _log.info("紧急退出：熔断，请求撤掉 %s 的买单（方案 %s）", order.symbol, order.plan_id)
        if self._breaker_day != judgement.day:
            self._breaker_day = judgement.day
            ratio = self._config.daily_loss_breaker_ratio
            self._notify(breaker_text(breaker, ratio, len(buys)))

    def advance(self, plan: TradePlan, judgement: Judgement) -> None:
        """一只股票的清仓，见 spec「一只股票的清仓」和 design D5。锁里做事，锁外告警。"""
        record = self._records[plan.plan_id]
        with self._services.lock:
            step = self._advance(plan, judgement.snapshot)
        _log_step(plan, step)
        if not record.announced:
            assert record.trigger is not None  # 只有找回来的记录没有触发的原因，它们一开始就告警过
            record.announced = True
            self._notify(start_text(record.trigger, step))
        elif step.kind is StepKind.NO_SHARES:
            self._notify(no_shares_text(plan.symbol))
        if step.kind is StepKind.NO_SHARES:
            record.ended = True

    def report(self) -> None:
        """报告结果，见 spec「告警」和 design D8：清仓单进入终态时告警；收盘以后还没有清仓单的，
        拿锁释放清仓方案剩下的预留再告警。盘外也做。
        """
        in_session = self._calendar.session_at(self._clock.now()) is not None
        execution = self._services.execution
        for record in [r for r in self._records.values() if not r.ended]:
            order = execution.order(record.plan.plan_id)
            if order is not None:
                if order.status.is_terminal:
                    record.ended = True
                    self._notify(outcome_text(order))
            elif not in_session:
                with self._services.lock:
                    self._services.risk.release(record.plan.plan_id)
                record.ended = True
                self._notify(missed_text(record.plan.symbol))

    def trigger_of(self, plan: TradePlan) -> Trigger | None:
        """这次运行里这个清仓方案第一次触发的原因；没有记录、或者是重启后找回的为 None。"""
        record = self._records.get(plan.plan_id)
        return None if record is None else record.trigger

    def _advance(self, plan: TradePlan, snapshot: AccountSnapshot) -> ExitStep:
        """在锁里：已有清仓单就不动；作废还没下单的方案；有挂着的订单就撤单、等它们结束；
        否则检查、再校验、提交清仓方案。见 design D5、D6。
        """
        services = self._services
        execution, risk = services.execution, services.risk
        if execution.order(plan.plan_id) is not None:
            return ExitStep(StepKind.HAS_ORDER)
        voided = tuple(
            p
            for p in risk.active_plans()
            if p.symbol == plan.symbol
            and p.plan_id != plan.plan_id
            and execution.order(p.plan_id) is None
        )
        for other in voided:
            risk.release(other.plan_id)
        waiting = tuple(o for o in execution.open_orders() if o.symbol == plan.symbol)
        if waiting:
            cancelled = tuple(o for o in waiting if not o.cancel_wanted)
            if cancelled:
                execution.cancel_orders(plan.symbol)
            return ExitStep(StepKind.WAITING, voided, cancelled, waiting)
        breaker = services.emergency.breaker_tripped()
        degraded = services.health.status.degraded
        decision = risk.check(plan, snapshot, daily_loss_breaker_tripped=breaker, degraded=degraded)
        if decision.outcome is not RiskOutcome.REJECTED:
            # 退出方案不看最新价，和下单流程一样传 0
            decision = risk.recheck(
                plan.plan_id, snapshot, ZERO, daily_loss_breaker_tripped=breaker, degraded=degraded
            )
        if decision.outcome is RiskOutcome.REJECTED:
            return ExitStep(StepKind.NO_SHARES, voided)
        order = execution.submit(plan, decision)
        return ExitStep(StepKind.SUBMITTED, voided, order=order)

    def _notify(self, text: str) -> None:
        """推送告警，同样的文字写一条 WARNING。调用时不拿锁。"""
        _log.warning("%s", text)
        self._notification.alert(text)

    def _quotes(self, account: Account) -> Mapping[str, Quote]:
        """一次取全部持仓的行情；不可用时当作一只都没有，缺的股票写一条 WARNING。"""
        symbols = [p.symbol for p in account.positions]
        try:
            quotes = self._services.market_data.quotes(symbols)
        except QuoteUnavailableError as error:
            _log.warning("紧急退出：行情不可用（%s），这一轮只判定熔断", error)
            return {}
        missing = [s for s in symbols if s not in quotes]
        if missing:
            _log.warning(
                "紧急退出：行情源没有 %s 的数据，这一轮不判定它们的个股跌幅和本地备用止损",
                "、".join(missing),
            )
        return quotes

    def _backup_stop(self, position: HeldPosition, holding: Holding, day: date) -> Trigger | None:
        """本地备用止损该触发时，这只股票当天的清仓方案，见 design D4。没有最新价时不判断。"""
        last = holding.last_price
        execution = self._services.execution
        if last is None or not execution.backup_stop_due(position.symbol, last):
            return None
        protection = execution.protection(position.symbol)
        unprotected = protection is not None and protection.unprotected_since is not None
        state = "无保护仓位" if unprotected else "错开中"
        detail = f"本地备用止损（{state}），最新价 {last} 不高于止损价 {position.stop_price}"
        plan = close_plan(position.symbol, position.sector, day)
        return Trigger(plan, ExitCause.BACKUP_STOP, detail)

    def _remember(self, triggers: Iterable[Trigger], account: Account, day: date) -> None:
        """记下这一轮新触发的股票；重启以后找回有持仓、当天的清仓单还没结束的股票，见 design D2。

        当天的清仓方案已经有结束了的订单的股票，当天的紧急退出已经做过，不再记。
        """
        execution = self._services.execution
        for trigger in triggers:
            plan_id = trigger.plan.plan_id
            if plan_id in self._records:
                continue
            order = execution.order(plan_id)
            if order is None:
                self._records[plan_id] = _Record(trigger.plan, day, trigger, announced=False)
            elif not order.status.is_terminal:
                self._records[plan_id] = _Record(trigger.plan, day, None, announced=True)
        for position in account.positions:
            plan = close_plan(position.symbol, position.sector, day)
            if plan.plan_id in self._records:
                continue
            order = execution.order(plan.plan_id)
            if order is not None and not order.status.is_terminal:
                self._records[plan.plan_id] = _Record(plan, day, None, announced=True)

    def _pending(self, day: date) -> tuple[TradePlan, ...]:
        """这一轮要推进的清仓方案：当天开始、没有结束、还没有订单的，按代码排序。"""
        execution = self._services.execution
        plans = (
            r.plan
            for r in self._records.values()
            if r.day == day and not r.ended and execution.order(r.plan.plan_id) is None
        )
        return tuple(sorted(plans, key=lambda p: p.symbol))

    def _forget_before(self, day: date) -> None:
        """删掉更早交易日的记录；正常都已经在 report 里结束，没结束的写一条 WARNING。"""
        for plan_id, record in list(self._records.items()):
            if record.day < day:
                if not record.ended:
                    _log.warning("紧急退出：%s %s 的记录没有结束", record.plan.symbol, record.day)
                del self._records[plan_id]


def _cause_text(cause: ExitCause, holding: Holding) -> str:
    """告警里的原因：个股跌幅写参考价、最新价、跌幅和紧急阈值；组合熔断只写原因。"""
    if cause is ExitCause.BREAKER:
        return "组合熔断"
    reference, last = reference_price(holding), holding.last_price
    assert reference is not None and last is not None  # 触及个股跌幅的持仓都有这两个价格
    drop = (reference - last) / reference
    return (
        f"个股跌幅 {drop:.1%}（参考价 {reference} → 最新价 {last}），"
        f"达到紧急阈值 {holding.emergency_threshold:.1%}"
    )


def start_text(trigger: Trigger, step: ExitStep) -> str:
    """开始紧急退出的告警：原因，加上这一项做了什么，见 design D8。"""
    symbol = trigger.plan.symbol
    parts = [f"紧急退出 {symbol}：{trigger.detail}。"]
    if step.voided:
        plans = "、".join(
            f"{ACTIONS[p.action]} {p.symbol}（方案 {p.plan_id}）" for p in step.voided
        )
        parts.append(f"作废还没下单的方案：{plans}。")
    if step.cancelled:
        orders = "、".join(
            f"{SIDES[o.side]} {o.quantity} 股（方案 {o.plan_id}）" for o in step.cancelled
        )
        parts.append(f"撤掉挂着的订单：{orders}。")
    match step.kind:
        case StepKind.SUBMITTED:
            assert step.order is not None
            parts.append(f"已建立清仓单：市价卖出 {step.order.quantity} 股")
        case StepKind.NO_SHARES:
            parts.append("没有可卖的股数，紧急退出结束")
        case StepKind.WAITING:
            parts.append("订单都结束以后市价清仓")
        case StepKind.HAS_ORDER:
            parts.append("已经有清仓单")
    return "".join(parts)


def breaker_text(breaker: Breaker, ratio: Decimal, cancelled: int) -> str:
    """熔断告警，见 design D8。"""
    previous, equity = breaker.previous_close_equity, breaker.equity
    loss = previous - equity
    return (
        f"单日亏损熔断：前一交易日收盘时的权益 {previous:,}，当前权益 {equity:,}，"
        f"单日亏损 {loss:,}（{loss / previous:.1%}），达到熔断比例 {ratio:.1%}。"
        f"清仓全部持仓，撤掉全部买单（{cancelled} 笔），收盘前不再开仓"
    )


def outcome_text(order: Order) -> str:
    """清仓单进入终态的告警：全部成交写股数和均价，其他写状态和成交股数。"""
    symbol = order.symbol
    if order.status is OrderStatus.FILLED:
        return f"{symbol} 紧急清仓完成：卖出 {order.filled} 股，成交均价 {order.average_price}"
    return (
        f"{symbol} 紧急清仓单{ORDER_STATUSES[order.status]}：成交 {order.filled} / "
        f"{order.quantity} 股。今天不再清仓 {symbol}，剩下的持仓由止损单保护"
    )


def no_shares_text(symbol: str) -> str:
    return f"{symbol} 没有可卖的股数，紧急退出结束"


def missed_text(symbol: str) -> str:
    return f"{symbol} 到收盘还没下清仓单（等撤单结束），今天不再清仓 {symbol}"


def _log_step(plan: TradePlan, step: ExitStep) -> None:
    """这一项的 INFO 日志：作废的方案、撤单请求或者在等的订单、建立的清仓单。"""
    symbol = plan.symbol
    for other in step.voided:
        _log.info("紧急退出 %s：作废方案 %s，释放预留", symbol, other.plan_id)
    match step.kind:
        case StepKind.WAITING if step.cancelled:
            _log.info("紧急退出 %s：请求撤掉 %d 笔挂着的订单", symbol, len(step.cancelled))
        case StepKind.WAITING:
            _log.info("紧急退出 %s：等 %d 笔挂着的订单结束", symbol, len(step.waiting))
        case StepKind.SUBMITTED:
            assert step.order is not None
            _log.info(
                "紧急退出 %s：建立清仓单（方案 %s），卖出 %d 股，%s",
                symbol,
                plan.plan_id,
                step.order.quantity,
                ORDER_STATUSES[step.order.status],
            )
