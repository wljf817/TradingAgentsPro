"""下单流程每一步的用例：首次检查、请求确认、确认的结果、等开盘、再校验和提交、到期作废。

每一步都是普通的方法，不 import dbos；DBOS 工作流在 order_workflow.py 里，只按这里的结果分支。
见 order-flow spec 和 add-order-flow 的 design。执行模块和风控里会改变状态的调用都拿着全程序
共用的那把锁（session-patrol spec）；请求确认、报告结果、推送告警时不拿锁。每一步的结果写进
程序日志，见 program-log spec 和 add-program-log 的 design D3、D9；等重试只写 Python 日志。

案件的方案（order-flow spec「案件的方案」，update-order-flow-case-plans 的 design D1、D2、D4、D6）：
首次检查时按确认规则决定要不要人工确认，不要的写自动执行；之后的步骤带着来源（CaseSource）：确认
消息附上案件的意见，退出方案确认超时以后告警，订单记下来源案件。/plan 的方案来源为 None。
"""

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.caserecords import CaseRecordService
from tradingagentspro.modules.emergency import EmergencyService
from tradingagentspro.modules.execution import (
    BrokerUnavailableError,
    ExecutionService,
    NotReconciledError,
    Order,
    OrderPausedError,
    OrderStatus,
    ReconcileMismatchError,
    UnprotectedPositionError,
)
from tradingagentspro.modules.health import HealthService
from tradingagentspro.modules.marketdata import MarketDataService
from tradingagentspro.modules.notification import (
    CaseConfirmation,
    CaseNote,
    ConfirmationOutcome,
    Disputed,
    NotificationConfig,
    NotificationService,
    NotificationUnavailableError,
    Unreviewed,
    case_plan_confirmation,
)
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
from tradingagentspro.modules.risk import RiskService
from tradingagentspro.modules.scheduler import CriticOutcome
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import OutsideSessionError, TradingCalendar
from tradingagentspro.workflows.account_snapshot import read_snapshot
from tradingagentspro.workflows.entries import (
    check_text,
    log_submit,
    recheck_text,
    release,
    trade_text,
)
from tradingagentspro.workflows.names import ACTIONS, ORDER_STATUSES

_log = logging.getLogger(__name__)

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class TradeServices:
    """下单流程用到的交易侧用例、全程序共用的那把锁和程序日志。"""

    execution: ExecutionService
    risk: RiskService
    emergency: EmergencyService
    health: HealthService
    market_data: MarketDataService
    lock: threading.RLock
    log: ProgramLog


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
class CaseSource:
    """案件的方案从哪个案件来，交接时的决定，见 order-flow spec「案件的方案」。"""

    case_id: UUID
    critic: CriticOutcome  # Critic 审核的结果
    confirmation: CaseConfirmation  # 要不要人工确认、确认超时以后要不要告警


@dataclass(frozen=True, slots=True)
class CaseCheck:
    """案件的方案首次检查的结果；source 只在通过或调整时有，拒绝时为 None。"""

    decision: RiskDecision
    source: CaseSource | None


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
    """首次检查，见 order-flow spec「提交方案」第 1、2 步；写程序日志的风控检查。

    读账户数据和行情（不拿锁），拿锁检查并预留。

    读券商账户失败时 BrokerUnavailableError 原样抛出。
    """
    decision, _ = _check(services, plan)
    return decision


def _check(services: TradeServices, plan: TradePlan) -> tuple[RiskDecision, bool]:
    """首次检查并写风控检查；同时给出检查时读到的是否处于降级模式。"""
    snapshot, _ = read_snapshot(services.execution, services.market_data)
    breaker = services.emergency.breaker_tripped()
    degraded = services.health.status.degraded
    with services.lock:
        decision = services.risk.check(
            plan, snapshot, daily_loss_breaker_tripped=breaker, degraded=degraded
        )
    services.log.write(Actor.RISK, Action.RISK_CHECK, str(plan.plan_id), check_text(plan, decision))
    return decision, degraded


class OrderFlow:
    """下单流程每一步的用例，见 order-flow spec。每个方法对应工作流的一个 step，见 design D2。"""

    def __init__(
        self,
        services: TradeServices,
        notification: NotificationService,
        notification_config: NotificationConfig,
        case_records: CaseRecordService,
        clock: Clock,
        calendar: TradingCalendar,
        config: OrderFlowConfig,
    ) -> None:
        self._services = services
        self._notification = notification
        self._notification_config = notification_config
        self._case_records = case_records
        self._clock = clock
        self._calendar = calendar
        self._config = config

    @property
    def config(self) -> OrderFlowConfig:
        return self._config

    def check_case_plan(
        self, case_id: UUID, plan: TradePlan, critic: CriticOutcome
    ) -> CaseCheck | None:
        """案件的方案首次检查并预留，见 spec「案件的方案」；读券商账户失败时写 WARNING、返回 None。

        通过或调整时按确认规则决定要不要人工确认（降级用检查时读到的），不要确认的写一条程序日志的
        自动执行（program-log spec「系统的动作」）。
        """
        services = self._services
        try:
            decision, degraded = _check(services, plan)
        except BrokerUnavailableError as error:
            _log.warning("%s：读券商账户失败（%s），风控检查出错", _head(plan), error)
            return None
        if decision.outcome is RiskOutcome.REJECTED:
            return CaseCheck(decision, None)
        confirmation = case_plan_confirmation(
            plan.action,
            critic_approved=critic is CriticOutcome.APPROVED,
            degraded=degraded,
            config=self._notification_config,
        )
        if not confirmation.required:
            services.log.write(
                Actor.SYSTEM, Action.AUTO_EXECUTE, str(plan.plan_id), auto_execute_text(plan)
            )
        return CaseCheck(decision, CaseSource(case_id, critic, confirmation))

    def request_confirmation(
        self, plan: TradePlan, decision: RiskDecision, source: CaseSource | None
    ) -> Requested | None:
        """请求人工确认，见 spec「请求人工确认」：通知模块写程序日志的请求确认。

        案件的方案按 Critic 审核的结果从案件记录组出意见，附在确认消息里。发不出去时写 ERROR，
        返回 None。不拿锁。
        """
        note = None if source is None else self._note(source)
        try:
            pending = self._notification.request_confirmation(plan, decision, note)
        except NotificationUnavailableError as error:
            _log.error("%s：确认请求没有发出去（%s），按确认超时处理", _head(plan), error)
            return None
        wait = max(0.0, (pending.deadline - self._clock.now()).total_seconds())
        return Requested(pending.message_id, pending.deadline, wait)

    def settle(
        self,
        plan: TradePlan,
        decision: RiskDecision,
        stage: Stage,
        requested: Requested | None,
        source: CaseSource | None,
    ) -> None:
        """按确认的结果处理，见 spec「确认的结果」：拒绝、超时、发不出去时释放全部预留。

        requested 是请求确认那一步的结果，发不出去时为 None。写程序日志的人的确认、拒绝或超时，
        以及释放预留，见 program-log spec「人的动作」。案件的方案确认超时以后要告警的（退出类），
        超时和发不出去时在释放以后推送告警，不拿锁。
        """
        log, subject = self._services.log, str(plan.plan_id)
        trade = trade_text(plan, None)
        match stage:
            case Stage.CONFIRMED:
                assert requested is not None  # 确认只发生在发出了确认消息以后
                log.write(
                    Actor.HUMAN, Action.CONFIRM, subject, trade, inputs=[requested.message_id]
                )
                return
            case Stage.TIMED_OUT:
                assert requested is not None  # 超时只发生在发出了确认消息以后
                log.write(
                    Actor.HUMAN,
                    Action.TIMEOUT,
                    subject,
                    f"{trade}：截止 {_utc(requested.deadline)}",
                    inputs=[requested.message_id],
                )
                self._notification.finish_confirmation(
                    plan.plan_id, requested.message_id, ConfirmationOutcome.TIMED_OUT
                )
                reason = "确认超时"
            case Stage.REJECTED:
                assert requested is not None  # 拒绝只发生在发出了确认消息以后
                log.write(Actor.HUMAN, Action.REJECT, subject, trade, inputs=[requested.message_id])
                reason = "人工拒绝"
            case _:
                reason = "确认请求没有发出去，按确认超时处理"
        with self._services.lock:
            release(self._services.risk, log, plan.plan_id, reason)
        unconfirmed = stage in (Stage.TIMED_OUT, Stage.UNSENT)
        if unconfirmed and source is not None and source.confirmation.alert_on_timeout:
            self._notification.alert(
                str(plan.plan_id), unconfirmed_text(plan, decision, timed_out=requested is not None)
            )

    def timing(self, plan: TradePlan, decision: RiskDecision) -> Timing:
        """到期、盘中还是要等，见 spec「等开盘」。"""
        now = self._clock.now()
        assert decision.expires_at is not None  # 通过或调整的结果都有生效的有效期
        if now >= decision.expires_at:
            return Timing(expired=True, wait_seconds=0)
        if self._calendar.session_at(now) is not None:
            return Timing(expired=False, wait_seconds=0)
        wake = self._calendar.next_session_after(now).open
        self._services.log.write(
            Actor.EXECUTION, Action.WAIT_OPEN, str(plan.plan_id), f"等到 {_utc(wake)} 开盘"
        )
        return Timing(expired=False, wait_seconds=(wake - now).total_seconds())

    def attempt(
        self,
        plan: TradePlan,
        decision: RiskDecision,
        source: CaseSource | None,
        *,
        alert_error: bool,
    ) -> Attempt:
        """一次下单前再校验和提交，见 spec「下单前再校验和提交」「提交的结果」和 design D5、D6。

        decision 是首次检查的结果，只用来写告警；source 是案件的方案的来源，新建的订单记下它的
        案件 ID。意料之外的错误写 ERROR；alert_error 为真时推送出错的告警。
        """
        try:
            return self._attempt(plan, decision, None if source is None else source.case_id)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            seconds = self._config.retry_delay.total_seconds()
            _log.exception("%s：下单出错，%g 秒后重试", _head(plan), seconds)
            if alert_error:
                self._notification.alert(
                    str(plan.plan_id), error_text(plan, decision, reason, self._config)
                )
            return Attempt(AttemptKind.RETRY, reason, alerted=alert_error)

    def void_expired(self, plan: TradePlan, decision: RiskDecision, failure: str | None) -> PlanEnd:
        """到了有效期还没下单：作废、释放、告警。已经有订单时不释放、不告警，交给盘中巡检。"""
        services = self._services
        assert decision.expires_at is not None
        reason = f"到了有效期 {_utc(decision.expires_at)} 还没下单"
        if failure is not None:
            reason += f"；最近一次没能下单的原因：{failure}"
        with services.lock:
            order = services.execution.order(plan.plan_id)
            if order is None:
                release(services.risk, services.log, plan.plan_id, f"方案作废，{reason}")
        if order is not None:
            self._hand_over(plan, order)
            return PlanEnd.SUBMITTED
        self._notification.alert(str(plan.plan_id), void_text(plan, decision, [reason]))
        return PlanEnd.VOIDED

    def _hand_over(self, plan: TradePlan, order: Order) -> None:
        """这个方案已经有订单：写一条提交订单，说明交给盘中巡检。"""
        self._services.log.write(
            Actor.EXECUTION,
            Action.SUBMIT,
            str(plan.plan_id),
            f"已经有订单（{ORDER_STATUSES[order.status]}），交给盘中巡检",
        )

    def _note(self, source: CaseSource) -> CaseNote | None:
        """确认消息附上的案件的意见，见 spec「请求人工确认」的表。"""
        match source.critic:
            case CriticOutcome.APPROVED:
                return None
            case CriticOutcome.UNAVAILABLE:
                return Unreviewed()
            case CriticOutcome.DISPUTED:
                conclusion = self._case_records.case(source.case_id).conclusion
                proposal = self._case_records.proposal(source.case_id)
                assert conclusion is not None  # 有方案的案件都写过结论和方案记录
                assert proposal is not None
                return Disputed(
                    rationale=conclusion.conclusion.rationale,
                    reviews=tuple(r.review for r in proposal.reviews),
                    responses=tuple(r.response for r in proposal.responses),
                )

    def _attempt(self, plan: TradePlan, decision: RiskDecision, case_id: UUID | None) -> Attempt:
        services = self._services
        log, subject = services.log, str(plan.plan_id)
        existing = services.execution.order(plan.plan_id)
        if existing is not None:
            self._hand_over(plan, existing)
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
                return self._next_open()
            # 再校验的结果在作废或者提交了订单时才写；转去等重试、等开盘的那几次不写
            if rechecked.outcome is RiskOutcome.REJECTED:
                log.write(Actor.RISK, Action.RECHECK, subject, recheck_text(rechecked))
                reasons = [f"- {v.rule}：{v.message}" for v in rechecked.violations]
            else:
                try:
                    order = services.execution.submit(plan, rechecked, case_id=case_id)
                except (NotReconciledError, OrderPausedError, UnprotectedPositionError) as error:
                    return self._retry(plan, str(error))
                except OutsideSessionError:
                    return self._next_open()
                except ReconcileMismatchError:
                    log.write(Actor.RISK, Action.RECHECK, subject, recheck_text(rechecked))
                    mismatch = "最近一次对账有不一致，暂停开仓"
                    release(services.risk, log, plan.plan_id, f"方案作废，{mismatch}")
                    reasons = [mismatch]
                else:
                    log.write(Actor.RISK, Action.RECHECK, subject, recheck_text(rechecked))
        if order is None:
            self._notification.alert(str(plan.plan_id), void_text(plan, decision, reasons))
            return Attempt(AttemptKind.VOIDED)
        log_submit(log, plan, order)
        if order.status is OrderStatus.REJECTED:
            self._notification.alert(
                str(plan.plan_id),
                f"下单被券商拒绝：{trade_text(plan, decision.quantity)}（方案 ID {plan.plan_id}）",
            )
        return Attempt(AttemptKind.SUBMITTED)

    def _retry(self, plan: TradePlan, reason: str) -> Attempt:
        seconds = self._config.retry_delay.total_seconds()
        _log.warning("%s：没能下单（%s），%g 秒后重试", _head(plan), reason, seconds)
        return Attempt(AttemptKind.RETRY, reason)

    def _next_open(self) -> Attempt:
        """已经不在交易时段：回到等开盘，接着的等开盘写等到的时刻。"""
        return Attempt(AttemptKind.NEXT_OPEN, "不在交易时段")


def void_text(plan: TradePlan, decision: RiskDecision, reasons: Sequence[str]) -> str:
    """方案作废的告警：动作、股票、确认的股数、方案 ID，每个原因一行。"""
    head = f"方案作废，不会下单：{trade_text(plan, decision.quantity)}（方案 ID {plan.plan_id}）"
    return "\n".join([head, *reasons])


def error_text(
    plan: TradePlan, decision: RiskDecision, reason: str, config: OrderFlowConfig
) -> str:
    """下单出错的告警。"""
    seconds = config.retry_delay.total_seconds()
    return (
        f"下单出错：{trade_text(plan, decision.quantity)}（方案 ID {plan.plan_id}）：{reason}。"
        f"每 {seconds:g} 秒重试一次，到有效期为止"
    )


def auto_execute_text(plan: TradePlan) -> str:
    """自动执行的说明：动作和股票，写明没有经过人工确认。"""
    return f"{ACTIONS[plan.action]} {plan.symbol}：按模型退出自动执行，没有经过人工确认"


def unconfirmed_text(plan: TradePlan, decision: RiskDecision, *, timed_out: bool) -> str:
    """案件的退出方案确认超时或确认请求发不出去的告警，见 spec「告警」的最后一行。"""
    head = "确认超时" if timed_out else "确认请求没有发出去"
    return (
        f"{head}，方案没有执行：{trade_text(plan, decision.quantity)}（方案 ID {plan.plan_id}）；"
        "持仓的止损单照挂"
    )


def _head(plan: TradePlan) -> str:
    """日志的开头：下单流程、方案 ID、动作和股票。"""
    return f"下单流程 {plan.plan_id}（{ACTIONS[plan.action]} {plan.symbol}）"


def _utc(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
