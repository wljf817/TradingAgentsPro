"""下单流程每一步的用例：首次检查、请求确认、确认的结果、等开盘、再校验和提交、到期作废。

每一步都是普通的方法，不 import dbos；DBOS 工作流在 order_workflow.py 里，只按这里的结果分支。
见 order-flow spec 和 add-order-flow 的 design。执行模块和风控里会改变状态的调用都拿着全程序
共用的那把锁（session-patrol spec）；请求确认、报告结果、推送告警时不拿锁。
"""

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.emergency import EmergencyService
from tradingagentspro.modules.execution import (
    BrokerUnavailableError,
    ExecutionService,
    NotReconciledError,
    OrderPausedError,
    OrderStatus,
    ReconcileMismatchError,
    UnprotectedPositionError,
)
from tradingagentspro.modules.health import HealthService
from tradingagentspro.modules.marketdata import MarketDataService
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    NotificationService,
    NotificationUnavailableError,
)
from tradingagentspro.modules.risk import RiskService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import OutsideSessionError, TradingCalendar
from tradingagentspro.workflows.account_snapshot import read_snapshot
from tradingagentspro.workflows.names import ACTIONS

_log = logging.getLogger(__name__)

ZERO = Decimal(0)

_OUTCOMES = {
    RiskOutcome.PASSED: "通过",
    RiskOutcome.ADJUSTED: "调整",
    RiskOutcome.REJECTED: "拒绝",
}
_ORDER_STATUSES = {
    OrderStatus.PENDING: "待提交",
    OrderStatus.SUBMITTED: "已提交",
    OrderStatus.PARTIALLY_FILLED: "部分成交",
    OrderStatus.FILLED: "全部成交",
    OrderStatus.CANCELLED: "已撤销",
    OrderStatus.EXPIRED: "已过期",
    OrderStatus.REJECTED: "已拒绝",
    OrderStatus.UNKNOWN: "状态未知",
}


@dataclass(frozen=True, slots=True)
class TradeServices:
    """下单流程用到的交易侧用例和全程序共用的那把锁。"""

    execution: ExecutionService
    risk: RiskService
    emergency: EmergencyService
    health: HealthService
    market_data: MarketDataService
    lock: threading.RLock


class OrderFlowConfig(BaseModel):
    """下单流程的配置和默认值，含义见 order-flow spec「下单流程配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 没能下单（暂停、无保护、取不到价格、读券商失败、意外的错）以后，等这么久再从再校验开始
    retry_delay: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))


class Stage(StrEnum):
    """确认的进展，也是工作流事件 confirmation 的值，见 design D3。"""

    WAITING = "waiting"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    UNSENT = "unsent"


class PlanEnd(StrEnum):
    """下单流程怎样结束：工作流的返回值。"""

    SUBMITTED = "submitted"  # 订单交给盘中巡检
    VOIDED = "voided"  # 确认以后作废
    REJECTED = "rejected"  # 人工拒绝
    TIMED_OUT = "timed_out"  # 确认超时
    UNSENT = "unsent"  # 确认请求发不出去


@dataclass(frozen=True, slots=True)
class Requested:
    """发出的确认消息，和请求以后到截止时刻还要等几秒。"""

    message_id: str
    deadline: datetime
    wait_seconds: float


@dataclass(frozen=True, slots=True)
class Timing:
    """确认以后、每次重试之前的判断：到了有效期，或者还要等几秒才开盘（0 是现在就在盘中）。"""

    expired: bool
    wait_seconds: float


class AttemptKind(StrEnum):
    """一次再校验和提交的结果，见 design D6。"""

    SUBMITTED = "submitted"  # 订单交给盘中巡检
    VOIDED = "voided"  # 方案作废，预留已释放
    RETRY = "retry"  # 等重试间隔再来
    NEXT_OPEN = "next_open"  # 不在交易时段，回到等开盘


@dataclass(frozen=True, slots=True)
class Attempt:
    """一次再校验和提交：结果、没能下单的原因、这一次是不是推送了出错的告警。"""

    kind: AttemptKind
    reason: str | None = None
    alerted: bool = False


def check_plan(services: TradeServices, plan: TradePlan) -> RiskDecision:
    """首次检查，见 order-flow spec「提交方案」第 1、2 步。

    读账户数据和行情（不拿锁），拿锁检查并预留。

    读券商账户失败时 BrokerUnavailableError 原样抛出。
    """
    snapshot, _ = read_snapshot(services.execution, services.market_data)
    breaker = services.emergency.breaker_tripped()
    degraded = services.health.status.degraded
    with services.lock:
        decision = services.risk.check(
            plan, snapshot, daily_loss_breaker_tripped=breaker, degraded=degraded
        )
    _log.info("%s：风控%s", _head(plan), _result(decision))
    return decision


class OrderFlow:
    """下单流程每一步的用例，见 order-flow spec。每个方法对应工作流的一个 step，见 design D2。"""

    def __init__(
        self,
        services: TradeServices,
        notification: NotificationService,
        clock: Clock,
        calendar: TradingCalendar,
        config: OrderFlowConfig,
    ) -> None:
        self._services = services
        self._notification = notification
        self._clock = clock
        self._calendar = calendar
        self._config = config

    @property
    def config(self) -> OrderFlowConfig:
        return self._config

    def request_confirmation(self, plan: TradePlan, decision: RiskDecision) -> Requested | None:
        """请求人工确认，见 spec「请求人工确认」。发不出去时写 ERROR，返回 None。不拿锁。"""
        try:
            pending = self._notification.request_confirmation(plan, decision)
        except NotificationUnavailableError as error:
            _log.error("%s：确认请求没有发出去（%s），按确认超时处理", _head(plan), error)
            return None
        wait = max(0.0, (pending.deadline - self._clock.now()).total_seconds())
        _log.info(
            "%s：请求确认，消息 %s，截止 %s",
            _head(plan),
            pending.message_id,
            _utc(pending.deadline),
        )
        return Requested(pending.message_id, pending.deadline, wait)

    def settle(
        self, plan: TradePlan, decision: RiskDecision, stage: Stage, message_id: str | None
    ) -> None:
        """按确认的结果处理，见 spec「确认的结果」：拒绝、超时、发不出去时释放全部预留。"""
        head = _head(plan)
        match stage:
            case Stage.CONFIRMED:
                _log.info("%s：已确认", head)
                return
            case Stage.TIMED_OUT:
                assert message_id is not None  # 超时只发生在发出了确认消息以后
                self._notification.finish_confirmation(message_id, ConfirmationOutcome.TIMED_OUT)
                text = "确认超时"
            case Stage.REJECTED:
                text = "已拒绝"
            case _:
                text = "确认请求没有发出去，按确认超时处理"
        with self._services.lock:
            self._services.risk.release(plan.plan_id)
        _log.info("%s：%s，释放预留", head, text)

    def timing(self, plan: TradePlan, decision: RiskDecision) -> Timing:
        """到期、盘中还是要等，见 spec「等开盘」。"""
        now = self._clock.now()
        assert decision.expires_at is not None  # 通过或调整的结果都有生效的有效期
        if now >= decision.expires_at:
            return Timing(expired=True, wait_seconds=0)
        if self._calendar.session_at(now) is not None:
            return Timing(expired=False, wait_seconds=0)
        wake = self._calendar.next_session_after(now).open
        _log.info("%s：不在交易时段，等到 %s 开盘", _head(plan), _utc(wake))
        return Timing(expired=False, wait_seconds=(wake - now).total_seconds())

    def attempt(self, plan: TradePlan, decision: RiskDecision, *, alert_error: bool) -> Attempt:
        """一次下单前再校验和提交，见 spec「下单前再校验和提交」「提交的结果」和 design D5、D6。

        decision 是首次检查的结果，只用来写告警。意料之外的错误写 ERROR；alert_error 为真时推送
        出错的告警。
        """
        try:
            return self._attempt(plan, decision)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            seconds = self._config.retry_delay.total_seconds()
            _log.exception("%s：下单出错，%g 秒后重试", _head(plan), seconds)
            if alert_error:
                self._notification.alert(error_text(plan, decision, reason, self._config))
            return Attempt(AttemptKind.RETRY, reason, alerted=alert_error)

    def void_expired(self, plan: TradePlan, decision: RiskDecision, failure: str | None) -> PlanEnd:
        """到了有效期还没下单：作废、释放、告警。已经有订单时不释放、不告警，交给盘中巡检。"""
        services = self._services
        with services.lock:
            order = services.execution.order(plan.plan_id)
            if order is None:
                services.risk.release(plan.plan_id)
        if order is not None:
            status = _ORDER_STATUSES[order.status]
            _log.info("%s：到了有效期，已经有订单（%s），交给盘中巡检", _head(plan), status)
            return PlanEnd.SUBMITTED
        assert decision.expires_at is not None
        reason = f"到了有效期 {_utc(decision.expires_at)} 还没下单"
        if failure is not None:
            reason += f"；最近一次没能下单的原因：{failure}"
        _log.info("%s：方案作废，%s", _head(plan), reason)
        self._notification.alert(void_text(plan, decision, [reason]))
        return PlanEnd.VOIDED

    def _attempt(self, plan: TradePlan, decision: RiskDecision) -> Attempt:
        services, head = self._services, _head(plan)
        existing = services.execution.order(plan.plan_id)
        if existing is not None:
            _log.info("%s：已经有订单（%s），交给盘中巡检", head, _ORDER_STATUSES[existing.status])
            return Attempt(AttemptKind.SUBMITTED)
        extra = (plan.symbol,) if plan.action.is_opening else ()
        try:
            snapshot, quotes = read_snapshot(services.execution, services.market_data, extra)
        except BrokerUnavailableError as error:
            return self._retry(plan, f"读券商账户失败：{error}")
        price = ZERO  # 风控不看退出方案的最新价
        if plan.action.is_opening:
            quote = quotes.get(plan.symbol)
            if quote is None:
                return self._retry(plan, f"取不到 {plan.symbol} 的行情")
            if quote.last_price is None:
                return self._retry(plan, f"{plan.symbol} 还没有当天的最新价")
            price = quote.last_price
        breaker = services.emergency.breaker_tripped()
        degraded = services.health.status.degraded
        reasons: list[str] = []  # 作废的原因；作废时才有
        order = None
        with services.lock:
            try:
                rechecked = services.risk.recheck(
                    plan.plan_id,
                    snapshot,
                    price,
                    daily_loss_breaker_tripped=breaker,
                    degraded=degraded,
                )
            except OutsideSessionError:
                return self._next_open(plan)
            _log.info("%s：再校验%s", head, _result(rechecked))
            if rechecked.outcome is RiskOutcome.REJECTED:
                reasons = [f"- {v.rule}：{v.message}" for v in rechecked.violations]
            else:
                try:
                    order = services.execution.submit(plan, rechecked)
                except (NotReconciledError, OrderPausedError, UnprotectedPositionError) as error:
                    return self._retry(plan, str(error))
                except OutsideSessionError:
                    return self._next_open(plan)
                except ReconcileMismatchError:
                    services.risk.release(plan.plan_id)
                    reasons = ["最近一次对账有不一致，暂停开仓"]
        if order is None:
            _log.info("%s：方案作废，释放预留：%s", head, "；".join(reasons))
            self._notification.alert(void_text(plan, decision, reasons))
            return Attempt(AttemptKind.VOIDED)
        status = _ORDER_STATUSES[order.status]
        _log.info("%s：订单%s，委托 %s 股", head, status, order.quantity)
        if order.status is OrderStatus.REJECTED:
            self._notification.alert(
                f"下单被券商拒绝：{_trade(plan, decision.quantity)}（方案 ID {plan.plan_id}）"
            )
        return Attempt(AttemptKind.SUBMITTED)

    def _retry(self, plan: TradePlan, reason: str) -> Attempt:
        seconds = self._config.retry_delay.total_seconds()
        _log.warning("%s：没能下单（%s），%g 秒后重试", _head(plan), reason, seconds)
        return Attempt(AttemptKind.RETRY, reason)

    def _next_open(self, plan: TradePlan) -> Attempt:
        _log.info("%s：已经不在交易时段，等下一个开盘", _head(plan))
        return Attempt(AttemptKind.NEXT_OPEN, "不在交易时段")


def void_text(plan: TradePlan, decision: RiskDecision, reasons: Sequence[str]) -> str:
    """方案作废的告警：动作、股票、确认的股数、方案 ID，每个原因一行。"""
    head = f"方案作废，不会下单：{_trade(plan, decision.quantity)}（方案 ID {plan.plan_id}）"
    return "\n".join([head, *reasons])


def error_text(
    plan: TradePlan, decision: RiskDecision, reason: str, config: OrderFlowConfig
) -> str:
    """下单出错的告警。"""
    seconds = config.retry_delay.total_seconds()
    return (
        f"下单出错：{_trade(plan, decision.quantity)}（方案 ID {plan.plan_id}）：{reason}。"
        f"每 {seconds:g} 秒重试一次，到有效期为止"
    )


def _trade(plan: TradePlan, quantity: int | None) -> str:
    """动作、股票和股数，比如「买入 BBB 20 股」；没有股数时不写。"""
    text = f"{ACTIONS[plan.action]} {plan.symbol}"
    return text if quantity is None else f"{text} {quantity} 股"


def _head(plan: TradePlan) -> str:
    """日志的开头：下单流程、方案 ID、动作和股票。"""
    return f"下单流程 {plan.plan_id}（{ACTIONS[plan.action]} {plan.symbol}）"


def _result(decision: RiskDecision) -> str:
    """风控结果，比如「通过，20 股」「拒绝：降级模式」。"""
    if decision.quantity is None:
        return f"{_OUTCOMES[decision.outcome]}：{'、'.join(v.rule for v in decision.violations)}"
    return f"{_OUTCOMES[decision.outcome]}，{decision.quantity} 股"


def _utc(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
