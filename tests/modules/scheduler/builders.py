"""调度器测试的数据：case-types spec「触发」里的基准数据。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tradingagentspro.contracts.agent import SubAgent
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
from tradingagentspro.contracts.trade_plan import OrderType

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
