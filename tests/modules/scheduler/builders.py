"""调度器测试的数据：case-types spec「触发」、case-queue spec「队列的输入和决定」和 case-stages spec
「案件的状态」里的基准数据。
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    CandidateConclusion,
    Decision,
    ExplorationConclusion,
    FollowUpReason,
    FollowUpRequest,
    HoldingConclusion,
    PlanDraft,
    WatchConclusion,
)
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.review import ResponseDecision, Verdict
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import (
    Catalyst,
    CatalystReference,
    Comparison,
    Condition,
    ConditionKind,
    InvestmentThesis,
    ReferenceKind,
    WatchThesis,
)
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction
from tradingagentspro.modules.scheduler import (
    CaseStage,
    CaseState,
    EventTrigger,
    FollowUpTrigger,
    HeartbeatTrigger,
    PendingPlan,
    PlanStage,
    QueuedCase,
    QueueSnapshot,
    RunningCase,
    SchedulerConfig,
    SellTrigger,
    Trigger,
    new_case,
)

INVESTMENT_THESIS = InvestmentThesis(
    rationale="新品周期带动收入加速",
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    target_price=Decimal(60),
    invalidation="毛利率连续两个季度下滑",
    invalidation_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(42)),
    ),
    review_day=date(2026, 10, 21),
)
# 和 stock-pool spec 的基准关注论点相同
WATCH_THESIS = WatchThesis(
    rationale="新品周期带动收入加速",
    entry_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(50)),
    ),
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    invalidation="毛利率连续两个季度下滑",
    review_day=date(2026, 10, 21),
)
# 基准方案条款
PLAN = PlanDraft(
    quantity=100,
    order_type=OrderType.LIMIT,
    limit_price=Decimal(50),
    stop_price=Decimal(45),
    emergency_threshold=Decimal("0.08"),
)
REDUCE_PLAN = PlanDraft(quantity=60, order_type=OrderType.MARKET)  # 60 股、市价单
CLOSE_PLAN = PlanDraft(order_type=OrderType.MARKET)  # 不写股数、市价单
FOLLOW_UP = FollowUpRequest(
    reason=FollowUpReason.EXTERNAL_SCHEDULE,
    reference=CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="S-9"),
    question="竞争对手 11 月 18 日发财报，之后对这只股票有什么影响？",
    possible_actions="减仓",
    trigger_at=datetime(2026, 11, 19, 14, 30, tzinfo=UTC),
)

# 本案件派过任务的子 agent；状态是完成的 Result 各自出自哪个子 agent
DISPATCHED = frozenset({SubAgent.NEWS, SubAgent.QUANT})
COMPLETED = {"R-1": SubAgent.NEWS, "R-2": SubAgent.QUANT}

# 结论的公共部分：回答、理由、评分 1、引用 R-1 和 R-2
_COMMON: dict[str, Any] = {
    "answer": "论点仍然成立",
    "rationale": "订单和毛利率都符合预期",
    "score": 1,
    "cited_results": ("R-1", "R-2"),
}


def holding(decision: Decision, **changes: Any) -> HoldingConclusion:
    """持仓的结论：公共部分加上决定，changes 覆盖或补充其中的字段。"""
    return HoldingConclusion.model_validate(_COMMON | {"decision": decision} | changes)


def watch(decision: Decision, **changes: Any) -> WatchConclusion:
    """关注的结论：公共部分加上决定，changes 覆盖或补充其中的字段。"""
    return WatchConclusion.model_validate(_COMMON | {"decision": decision} | changes)


def candidate(decision: Decision, **changes: Any) -> CandidateConclusion:
    """候选的结论：公共部分加上决定，changes 覆盖或补充其中的字段。"""
    return CandidateConclusion.model_validate(_COMMON | {"decision": decision} | changes)


def exploration(**changes: Any) -> ExplorationConclusion:
    """寻股的结论：公共部分（没有评分），changes 覆盖或补充其中的字段。"""
    common = {key: value for key, value in _COMMON.items() if key != "score"}
    return ExplorationConclusion.model_validate(common | changes)


# 队列的基准数据（case-queue spec「队列的输入和决定」）

STATUSES: Mapping[str, StockStatus] = {
    "AAA": StockStatus.CANDIDATE,
    "DDD": StockStatus.CANDIDATE,
    "BBB": StockStatus.WATCH,
    "CCC": StockStatus.HOLDING,
    "EEE": StockStatus.HOLDING,
}
_NAMESPACE = UUID(int=0)


def at(hour: int, minute: int = 0) -> datetime:
    """触发时刻：2026-10-14（周三）的这个 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, tzinfo=UTC)


def case_id(name: str) -> UUID:
    """按名字取固定的案件 ID，比如 case_id("CCC 的新闻事件")。"""
    return uuid5(_NAMESPACE, f"case/{name}")


# 触发：spec 场景里用到的几种


def news(symbol: str) -> EventTrigger:
    """新闻事件。"""
    return EventTrigger(symbol, EventCategory.NEWS, holding_risk=False)


def price_volume(symbol: str, holding_risk: bool = False) -> EventTrigger:
    """价格和成交量异动事件；holding_risk 是标注为持仓风险级别。"""
    return EventTrigger(symbol, EventCategory.PRICE_VOLUME, holding_risk=holding_risk)


def holding_review_due(symbol: str) -> HeartbeatTrigger:
    """持仓复查日到期。"""
    return HeartbeatTrigger(CaseType.HOLDING_REVIEW, symbol)


def watch_review_due(symbol: str) -> HeartbeatTrigger:
    """关注复查日到期。"""
    return HeartbeatTrigger(CaseType.WATCH_REVIEW, symbol)


def new_candidate(symbol: str) -> HeartbeatTrigger:
    """有新候选。"""
    return HeartbeatTrigger(CaseType.CANDIDATE_EVALUATION, symbol)


def follow_up(symbol: str) -> FollowUpTrigger:
    """获批的跟进到了触发时间。"""
    return FollowUpTrigger(symbol, "申请时写下的问题")


def sell_fill(symbol: str) -> SellTrigger:
    """卖出成交。"""
    return SellTrigger(symbol)


POOL_SHORT = HeartbeatTrigger(CaseType.EXPLORATION)  # 股票池候选不足
WEEKLY = HeartbeatTrigger(CaseType.WEEKLY_RETROSPECTIVE)  # 每周固定时间


def queued(name: str, trigger: Trigger, triggered_at: datetime) -> QueuedCase:
    """排队的案件，案件 ID 按名字取。"""
    return QueuedCase(case_id(name), trigger, triggered_at)


def running(
    name: str, case_type: CaseType, symbol: str | None, calling_models: bool = True
) -> RunningCase:
    """运行中的案件，案件 ID 按名字取；calling_models 为 False 表示已经过了调用模型的阶段。"""
    return RunningCase(case_id(name), case_type, symbol, calling_models)


# 方案：P-1 是 BBB 的买入方案，P-2 是 CCC 的加仓方案，P-3 是 CCC 的减仓方案
P_1, P_2, P_3 = (uuid5(_NAMESPACE, f"plan/{name}") for name in ("P-1", "P-2", "P-3"))
_PLANS: Mapping[UUID, tuple[str, TradeAction]] = {
    P_1: ("BBB", TradeAction.BUY),
    P_2: ("CCC", TradeAction.ADD),
    P_3: ("CCC", TradeAction.REDUCE),
}


def plan(plan_id: UUID, stage: PlanStage) -> PendingPlan:
    """基准方案 P-1 到 P-3 之一，处于 stage 阶段。"""
    symbol, action = _PLANS[plan_id]
    return PendingPlan(plan_id, symbol, action, stage)


def statuses(**changes: StockStatus | None) -> Mapping[str, StockStatus]:
    """基准的股票状态，changes 改变其中的股票；None 表示不在股票池也不是持仓。"""
    merged = {**STATUSES, **changes}
    return {symbol: status for symbol, status in merged.items() if status is not None}


def snapshot(
    queued: Iterable[QueuedCase] = (),
    running: Iterable[RunningCase] = (),
    plans: Iterable[PendingPlan] = (),
    statuses: Mapping[str, StockStatus] = STATUSES,
    exiting: Iterable[str] = (),
    *,
    degraded: bool = False,
    general_budget_exhausted: bool = False,
    exploration_budget_exhausted: bool = False,
) -> QueueSnapshot:
    """快照：默认是基准数据，没有排队和运行中的案件、没有方案、不降级、预算没用完。"""
    return QueueSnapshot(
        queued=tuple(queued),
        running=tuple(running),
        plans=tuple(plans),
        statuses=statuses,
        exiting=frozenset(exiting),
        degraded=degraded,
        general_budget_exhausted=general_budget_exhausted,
        exploration_budget_exhausted=exploration_budget_exhausted,
    )


# 案件阶段的基准数据（case-stages spec「案件的状态」）：开仓案件是 BBB（关注）的关注股事件，
# 结论是买入；退出案件是 CCC（持仓）的持仓事件，结论是减仓

type CaseEvent = Callable[[CaseState], CaseState]
DEFAULT_CONFIG = SchedulerConfig()


def opening_case() -> CaseState:
    """开仓案件开始：在唤醒 PM。"""
    return new_case(CaseType.WATCH_EVENT)


def exit_case() -> CaseState:
    """退出案件开始：在唤醒 PM。"""
    return new_case(CaseType.HOLDING_EVENT)


def receive(state: CaseState, *events: CaseEvent) -> list[CaseState]:
    """依次把事件交给案件状态，返回每个事件之后的状态，对应场景里的"依次收到"。"""
    states = []
    for case_event in events:
        state = case_event(state)
        states.append(state)
    return states


def after(state: CaseState, *events: CaseEvent) -> CaseState:
    """依次收到这些事件以后的状态。"""
    for case_event in events:
        state = case_event(state)
    return state


# 事件：不带参数的事件直接用 CaseState 的方法，比如 CaseState.confirm


def rejected(config: SchedulerConfig = DEFAULT_CONFIG) -> CaseEvent:
    """结论校验不通过。"""
    return lambda state: state.reject_conclusion(config)


def accepted(action: TradeAction | None) -> CaseEvent:
    """结论通过；action 是结论里交易方案的动作，没有方案时为 None。"""
    return lambda state: state.accept_conclusion(action)


def critic(verdict: Verdict, config: SchedulerConfig = DEFAULT_CONFIG) -> CaseEvent:
    """Critic 给出结论。"""
    return lambda state: state.review(verdict, config)


def pm(decision: ResponseDecision) -> CaseEvent:
    """PM 回应质询。"""
    return lambda state: state.respond(decision)


def risk(outcome: RiskOutcome) -> CaseEvent:
    """风控给出结果。"""
    return lambda state: state.risk_checked(outcome)


def _at(state: CaseState, action: TradeAction, stage: CaseStage) -> CaseState:
    """沿"结论通过、Critic 通过、风控通过、已确认"把案件推到 stage；PM 回应是 Critic 质询一次
    以后。
    """
    verdict = Verdict.CHALLENGE if stage is CaseStage.PM_RESPONSE else Verdict.APPROVE
    steps: tuple[CaseEvent, ...] = (
        accepted(action),
        critic(verdict),
        risk(RiskOutcome.PASSED),
        CaseState.confirm,
    )
    for step in steps:
        if state.stage is stage:
            break
        state = step(state)
    assert state.stage is stage  # 退出案件不会进入 PM 回应
    return state


def opening_at(stage: CaseStage) -> CaseState:
    """开仓案件（结论是买入）推到 stage；在 Critic 审核时还没有被质询过。"""
    return _at(opening_case(), TradeAction.BUY, stage)


def exit_at(stage: CaseStage) -> CaseState:
    """退出案件（结论是减仓）推到 stage。"""
    return _at(exit_case(), TradeAction.REDUCE, stage)
