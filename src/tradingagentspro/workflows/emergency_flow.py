"""紧急退出流程：盘中巡检每一轮里判定紧急风险退出和本地备用止损，撤单、作废还没下单的方案、清仓，
熔断时撤掉全部买单，执行后告警。

见 emergency-exit-flow spec 和 add-emergency-exit-flow 的 design。一轮里的每一项由巡检编排
（patrol.py 的 _emergency_exit）：judge 不拿锁；cancel_buys、advance 各拿一次全程序共用的那把锁；
report 只在释放预留时拿锁；推送告警都在锁外。

通知配置的「紧急退出要确认」打开时，个股跌幅和组合熔断触发的清仓先请求人工确认，确认以后才
清仓；本地备用止损不等确认。按钮的结果经 EmergencyDecisions 送到这里，见 emergency-exit-flow spec
「紧急退出的人工确认」和 update-confirmation-rules 的 design D2 到 D7。

熔断、紧急风险退出、撤单、作废的方案、清仓方案的检查和提交、人的确认结果写进程序日志；等订单结束的
轮次不写，见 program-log spec 和 add-program-log 的 design D9。
"""

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date
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
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    NotificationConfig,
    NotificationService,
    NotificationUnavailableError,
    PendingConfirmation,
)
from tradingagentspro.modules.programlog import ACCOUNT, Action, Actor
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows.account_snapshot import account_snapshot
from tradingagentspro.workflows.entries import (
    check_text,
    log_submit,
    order_changes,
    recheck_text,
    release,
)
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
    """一只股票这一项的结果：作废的方案、这一次撤掉的订单、建立的清仓单。"""

    kind: StepKind
    voided: tuple[TradePlan, ...] = ()
    cancelled: tuple[Order, ...] = ()
    order: Order | None = None


class Gate(StrEnum):
    """一只股票当天的紧急退出走到确认的哪一步，见 update-confirmation-rules 的 design D3。"""

    OPEN = "open"  # 不用确认，或者已经确认：按「一只股票的清仓」处理
    ASKING = "asking"  # 要请求确认
    WAITING = "waiting"  # 已经请求，在等结果
    DECLINED = "declined"  # 拒绝、超时或者发不出去：当天不再处理，本地备用止损除外


@dataclass
class _Record:
    """这次运行里当天开始的一只股票的紧急退出，见 add-emergency-exit-flow 的 design D2。

    trigger 是第一次触发的原因（本地备用止损越过确认以后是备用止损）；重启以后按清仓单找回来的
    为 None。announced：开始的告警已经推送过（找回来的一开始就是）。ended：当天的紧急退出已经结束，
    不再处理。gate 是确认走到哪一步；pending 是在等的确认消息，请求以后才有。
    """

    plan: TradePlan
    day: date
    trigger: Trigger | None
    announced: bool
    ended: bool = False
    gate: Gate = Gate.OPEN
    pending: PendingConfirmation | None = None


class EmergencyDecisions:
    """紧急退出在等的确认和送到的按钮结果，见 update-confirmation-rules 的 design D4。

    按钮的回调在通知通道的工作线程里调用 deliver，巡检线程调用其余三个方法，所以用一把锁。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: dict[UUID, ConfirmationOutcome | None] = {}

    def expect(self, plan_id: UUID) -> None:
        """开始等这个方案的结果。"""
        with self._lock:
            self._waiting[plan_id] = None

    def deliver(self, plan_id: UUID, outcome: ConfirmationOutcome) -> bool:
        """在等、还没有结果时记下这个结果并返回 True；否则不收下，返回 False。"""
        with self._lock:
            if plan_id not in self._waiting or self._waiting[plan_id] is not None:
                return False
            self._waiting[plan_id] = outcome
            return True

    def take(self, plan_id: UUID) -> ConfirmationOutcome | None:
        """有结果就取走并停止等；没有结果时返回 None，继续等。"""
        with self._lock:
            outcome = self._waiting.get(plan_id)
            if outcome is not None:
                del self._waiting[plan_id]
            return outcome

    def close(self, plan_id: UUID) -> ConfirmationOutcome | None:
        """停止等这个方案，返回已经送到的结果。"""
        with self._lock:
            return self._waiting.pop(plan_id, None)


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

    judge 不拿锁；cancel_buys、advance 各拿一次锁，告警和请求确认在锁外；report 只在释放预留时
    拿锁。只在巡检的线程里调用；按钮的结果经 decisions 从别的线程送来。
    """

    def __init__(
        self,
        services: TradeServices,
        notification: NotificationService,
        clock: Clock,
        calendar: TradingCalendar,
        config: EmergencyConfig,
        notification_config: NotificationConfig,
        decisions: EmergencyDecisions,
    ) -> None:
        self._services = services
        self._notification = notification
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._confirm = notification_config.confirm_emergency_exits
        self._decisions = decisions
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
        backups: dict[str, Trigger] = {}
        for position in account.positions:
            trigger = self._backup_stop(position, holdings[position.symbol], day)
            if trigger is not None:
                backups[position.symbol] = trigger
        self._remember(triggers, backups, account, day)
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

        见 spec「单日亏损熔断」和 design D7。每个交易日第一次时先写程序日志的熔断；撤掉的买单
        各写一条撤单，撤单以后的那次跟进有变化的写订单变化。
        """
        breaker = judgement.breaker
        assert breaker is not None  # 只在熔断已触发时调用
        services = self._services
        execution, log = services.execution, services.log
        ratio = self._config.daily_loss_breaker_ratio
        first = self._breaker_day != judgement.day
        if first:
            log.write(Actor.SYSTEM, Action.BREAKER, ACCOUNT, breaker_figures(breaker, ratio))
        with services.lock:
            buys = tuple(
                o
                for o in execution.open_orders()
                if o.side is OrderSide.BUY and not o.cancel_wanted
            )
            if buys:
                with order_changes(execution, log):
                    execution.cancel_buy_orders()
                    for order in buys:
                        log.write(
                            Actor.EXECUTION,
                            Action.CANCEL,
                            str(order.plan_id),
                            f"熔断：{cancel_text(order)}",
                        )
        if first:
            self._breaker_day = judgement.day
            self._notify(ACCOUNT, breaker_text(breaker, ratio, len(buys), self._confirm))

    def advance(self, plan: TradePlan, judgement: Judgement) -> None:
        """一只股票的清仓，见 spec「一只股票的清仓」「紧急退出的人工确认」和 design D5。

        要确认的先请求确认、等结果，确认的那一轮接着清仓（update-confirmation-rules 的 design D3）。
        锁里做事，锁外告警和请求确认。
        """
        record = self._records[plan.plan_id]
        if record.gate is Gate.ASKING:
            self._ask(record, judgement)
            return
        if record.gate is Gate.WAITING and not self._settle(record):
            return
        if not record.announced:
            assert record.trigger is not None  # 只有找回来的记录没有触发的原因，它们一开始就告警过
            self._services.log.write(
                Actor.SYSTEM,
                Action.EMERGENCY_EXIT,
                plan.symbol,
                record.trigger.detail,
                outputs=[str(plan.plan_id)],
            )
        with self._services.lock:
            step = self._advance(plan, judgement.snapshot)
        if not record.announced:
            assert record.trigger is not None  # 只有找回来的记录没有触发的原因，它们一开始就告警过
            record.announced = True
            self._notify(plan.symbol, start_text(record.trigger, step))
        elif step.kind is StepKind.NO_SHARES:
            self._notify(plan.symbol, no_shares_text(plan.symbol))
        if step.kind is StepKind.NO_SHARES:
            record.ended = True

    def report(self) -> None:
        """报告结果，见 spec「告警」和 design D8：清仓单进入终态时告警；收盘以后还没有清仓单的，
        拿锁释放清仓方案剩下的预留再告警。盘外也做。
        """
        in_session = self._calendar.session_at(self._clock.now()) is not None
        execution = self._services.execution
        for record in [r for r in self._records.values() if not r.ended]:
            if record.gate is not Gate.OPEN:
                if not in_session:
                    self._close_unconfirmed(record)
                continue
            order = execution.order(record.plan.plan_id)
            if order is not None:
                if order.status.is_terminal:
                    record.ended = True
                    self._notify(order.symbol, outcome_text(order))
            elif not in_session:
                symbol = record.plan.symbol
                with self._services.lock:
                    release(
                        self._services.risk,
                        self._services.log,
                        record.plan.plan_id,
                        f"{symbol} 到收盘还没有清仓单",
                    )
                record.ended = True
                self._notify(symbol, missed_text(symbol))

    def trigger_of(self, plan: TradePlan) -> Trigger | None:
        """这次运行里这个清仓方案第一次触发的原因；没有记录、或者是重启后找回的为 None。"""
        record = self._records.get(plan.plan_id)
        return None if record is None else record.trigger

    def _ask(self, record: _Record, judgement: Judgement) -> None:
        """请求紧急退出的确认，不拿锁；发不出去按超时处理。

        见 update-confirmation-rules 的 design D3。
        """
        plan = record.plan
        assert record.trigger is not None  # 要确认的记录都是这次运行里触发的
        quantity = next(p.quantity for p in judgement.snapshot.positions if p.symbol == plan.symbol)
        self._decisions.expect(plan.plan_id)
        try:
            pending = self._notification.request_emergency_confirmation(
                plan, quantity, record.trigger.detail
            )
        except NotificationUnavailableError as error:
            self._decisions.close(plan.plan_id)
            self._decline(record)
            _log.error("紧急退出 %s：确认请求没有发出去（%s）", plan.symbol, error)
            self._notify(plan.symbol, unsent_text(plan.symbol))
            return
        record.gate = Gate.WAITING
        record.pending = pending

    def _settle(self, record: _Record) -> bool:
        """在等确认：先看按钮的结果，再看截止时刻。返回这一轮是否接着清仓。

        收下的确认、拒绝和到截止时刻的超时写程序日志的人的动作。
        """
        plan, pending = record.plan, record.pending
        assert pending is not None  # 在等的记录都已经请求过
        log, subject = self._services.log, str(plan.plan_id)
        about = f"紧急退出 {plan.symbol}"
        outcome = self._decisions.take(plan.plan_id)
        if outcome is None and self._clock.now() >= pending.deadline:
            outcome = self._decisions.close(plan.plan_id)  # 关闭时刚好送到的结果照样采用
            if outcome is None:
                deadline = f"{pending.deadline.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
                log.write(
                    Actor.HUMAN,
                    Action.TIMEOUT,
                    subject,
                    f"{about}：截止 {deadline}",
                    inputs=[pending.message_id],
                )
                self._notification.finish_confirmation(
                    plan.plan_id, pending.message_id, ConfirmationOutcome.TIMED_OUT
                )
                self._decline(record)
                self._notify(plan.symbol, timeout_text(plan.symbol))
                return False
        if outcome is ConfirmationOutcome.CONFIRMED:
            log.write(Actor.HUMAN, Action.CONFIRM, subject, about, inputs=[pending.message_id])
            record.gate = Gate.OPEN
            return True
        if outcome is ConfirmationOutcome.REJECTED:
            log.write(Actor.HUMAN, Action.REJECT, subject, about, inputs=[pending.message_id])
            self._decline(record)
        return False

    def _bypass(self, record: _Record, trigger: Trigger) -> None:
        """本地备用止损越过确认：在等的确认消息作废；在等或已经拒绝、超时的改为接着清仓，
        原因换成本地备用止损。见 update-confirmation-rules 的 design D3。
        """
        if record.gate is Gate.WAITING:
            assert record.pending is not None
            self._decisions.close(record.plan.plan_id)
            message_id = record.pending.message_id
            self._notification.finish_confirmation(
                record.plan.plan_id, message_id, ConfirmationOutcome.VOIDED
            )
        elif record.gate is not Gate.DECLINED:
            return
        record.gate = Gate.OPEN
        record.trigger = trigger
        record.announced = False
        record.ended = False

    def _close_unconfirmed(self, record: _Record) -> None:
        """收盘以后还在等确认：确认消息作废，当天不再紧急退出，告警。"""
        self._decisions.close(record.plan.plan_id)
        if record.pending is not None:
            message_id = record.pending.message_id
            self._notification.finish_confirmation(
                record.plan.plan_id, message_id, ConfirmationOutcome.VOIDED
            )
        self._decline(record)
        self._notify(record.plan.symbol, unconfirmed_text(record.plan.symbol))

    def _decline(self, record: _Record) -> None:
        """拒绝、超时、发不出去或者收盘：当天的紧急退出结束，本地备用止损除外。"""
        record.gate = Gate.DECLINED
        record.ended = True

    def _advance(self, plan: TradePlan, snapshot: AccountSnapshot) -> ExitStep:
        """在锁里：已有清仓单就不动；作废还没下单的方案；有挂着的订单就撤单、等它们结束；
        否则检查、再校验、提交清仓方案。见 design D5、D6。

        每一步接着写程序日志：作废的方案写释放预留，新记下撤单要求的订单写撤单（撤单以后的那次
        跟进有变化的写订单变化），风控检查、下单前再校验、提交订单各写一条。
        """
        services = self._services
        execution, risk, log = services.execution, services.risk, services.log
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
            release(risk, log, other.plan_id, f"{plan.symbol} 紧急退出：作废还没下单的方案")
        waiting = tuple(o for o in execution.open_orders() if o.symbol == plan.symbol)
        if waiting:
            cancelled = tuple(o for o in waiting if not o.cancel_wanted)
            if cancelled:
                with order_changes(execution, log):
                    execution.cancel_orders(plan.symbol)
                    for order in cancelled:
                        log.write(
                            Actor.EXECUTION,
                            Action.CANCEL,
                            str(order.plan_id),
                            f"{plan.symbol} 紧急退出：{cancel_text(order)}",
                        )
            return ExitStep(StepKind.WAITING, voided, cancelled)
        breaker = services.emergency.breaker_tripped()
        degraded = services.health.status.degraded
        subject = str(plan.plan_id)
        decision = risk.check(plan, snapshot, daily_loss_breaker_tripped=breaker, degraded=degraded)
        log.write(Actor.RISK, Action.RISK_CHECK, subject, check_text(plan, decision))
        if decision.outcome is not RiskOutcome.REJECTED:
            # 退出方案不看最新价，和下单流程一样传 0
            decision = risk.recheck(
                plan.plan_id, snapshot, ZERO, daily_loss_breaker_tripped=breaker, degraded=degraded
            )
            log.write(Actor.RISK, Action.RECHECK, subject, recheck_text(decision))
        if decision.outcome is RiskOutcome.REJECTED:
            return ExitStep(StepKind.NO_SHARES, voided)
        order = execution.submit(plan, decision, case_id=None)
        log_submit(log, plan, order)
        return ExitStep(StepKind.SUBMITTED, voided, order=order)

    def _notify(self, subject: str, text: str) -> None:
        """推送告警，subject 是告警针对的对象；通知模块把它写进程序日志。调用时不拿锁。"""
        self._notification.alert(subject, text)

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

    def _remember(
        self,
        triggers: Mapping[str, Trigger],
        backups: Mapping[str, Trigger],
        account: Account,
        day: date,
    ) -> None:
        """记下这一轮新触发的股票；重启以后找回有持仓、当天的清仓单还没结束的股票，见 design D2。

        triggers 是紧急退出的判定，backups 是本地备用止损；一只股票两种都有时原因取紧急退出的。
        要确认时新记录从 ASKING 开始，备用止损触发的从 OPEN 开始；已有的记录遇到备用止损触发时
        越过确认（update-confirmation-rules 的 design D3）。当天的清仓方案已经有结束了的订单的
        股票，当天的紧急退出已经做过，不再记。
        """
        execution = self._services.execution
        for symbol, trigger in {**backups, **triggers}.items():
            plan_id = trigger.plan.plan_id
            record = self._records.get(plan_id)
            if record is not None:
                if symbol in backups:
                    self._bypass(record, backups[symbol])
                continue
            order = execution.order(plan_id)
            if order is None:
                gate = Gate.ASKING if self._confirm and symbol not in backups else Gate.OPEN
                self._records[plan_id] = _Record(
                    trigger.plan, day, trigger, announced=False, gate=gate
                )
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


def breaker_text(breaker: Breaker, ratio: Decimal, cancelled: int, confirm: bool) -> str:
    """熔断告警，见 design D8；confirm 是「紧急退出要确认」打开，写明清仓要确认。"""
    previous, equity = breaker.previous_close_equity, breaker.equity
    loss = previous - equity
    text = (
        f"单日亏损熔断：前一交易日收盘时的权益 {previous:,}，当前权益 {equity:,}，"
        f"单日亏损 {loss:,}（{loss / previous:.1%}），达到熔断比例 {ratio:.1%}。"
        f"清仓全部持仓，撤掉全部买单（{cancelled} 笔），收盘前不再开仓"
    )
    return text + "；每只持仓的清仓要确认" if confirm else text


def breaker_figures(breaker: Breaker, ratio: Decimal) -> str:
    """程序日志的熔断的说明：两个权益、单日亏损和它的比例、熔断比例。"""
    previous, equity = breaker.previous_close_equity, breaker.equity
    loss = previous - equity
    return (
        f"前一交易日收盘时的权益 {previous:,}，当前权益 {equity:,}，"
        f"单日亏损 {loss:,}（{loss / previous:.1%}），熔断比例 {ratio:.1%}"
    )


def cancel_text(order: Order) -> str:
    """撤单的说明：买卖方向、股票和委托股数，比如「撤掉卖出 AAA 20 股的订单」。"""
    return f"撤掉{SIDES[order.side]} {order.symbol} {order.quantity} 股的订单"


def outcome_text(order: Order) -> str:
    """清仓单进入终态的告警：全部成交写股数和均价，其他写状态和成交股数。"""
    symbol = order.symbol
    if order.status is OrderStatus.FILLED:
        return f"{symbol} 紧急清仓完成：卖出 {order.filled} 股，成交均价 {order.average_price}"
    return (
        f"{symbol} 紧急清仓单{ORDER_STATUSES[order.status]}：成交 {order.filled} / "
        f"{order.quantity} 股。今天不再清仓 {symbol}，剩下的持仓由止损单保护"
    )


def timeout_text(symbol: str) -> str:
    return f"{symbol} 紧急退出没有执行：到截止时刻没有确认。今天不再紧急退出 {symbol}，止损单照挂"


def unsent_text(symbol: str) -> str:
    return f"{symbol} 紧急退出没有执行：确认请求没有发出去。今天不再紧急退出 {symbol}，止损单照挂"


def unconfirmed_text(symbol: str) -> str:
    return f"{symbol} 紧急退出没有执行：到收盘还没有确认。今天不再紧急退出 {symbol}"


def no_shares_text(symbol: str) -> str:
    return f"{symbol} 没有可卖的股数，紧急退出结束"


def missed_text(symbol: str) -> str:
    return f"{symbol} 到收盘还没下清仓单（等撤单结束），今天不再清仓 {symbol}"
