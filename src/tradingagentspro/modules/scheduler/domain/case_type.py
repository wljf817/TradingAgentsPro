"""触发和案件类型：案件开始运行时，按触发和股票这时的状态确定案件类型。

规则见 case-types spec「触发」「案件类型」「开始运行时确定案件类型」「事件要求的子 agent」。
两张表就是 spec 的表。优先级、问题和事件对应的子 agent 是 Proposal 规定或人决定的规则，不是阈值，
不做成配置。
"""

from dataclasses import dataclass
from typing import Final

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    CandidateConclusion,
    Conclusion,
    ExplorationConclusion,
    HoldingConclusion,
    WatchConclusion,
)
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.stock_pool import StockStatus

_HOLDING, _WATCH, _CANDIDATE = StockStatus.HOLDING, StockStatus.WATCH, StockStatus.CANDIDATE


@dataclass(frozen=True, slots=True)
class _CaseSpec:
    """案件类型固定的内容，spec「案件类型」表的一行。"""

    status: StockStatus | None  # 针对的状态；寻股和复盘不看状态，为 None
    question: str | None  # 跟进用申请时写下的问题，复盘不唤醒 PM，都为 None
    conclusion: type[Conclusion] | None  # 复盘不唤醒 PM，没有结论
    priority: int  # 1 最高，9 最低


_EVENT_QUESTION = "这个事件是否削弱或推翻了论点？"

_CASE_TYPES: Final = {
    CaseType.HOLDING_RISK_EVENT: _CaseSpec(_HOLDING, _EVENT_QUESTION, HoldingConclusion, 1),
    CaseType.HOLDING_EVENT: _CaseSpec(_HOLDING, _EVENT_QUESTION, HoldingConclusion, 2),
    CaseType.HOLDING_REVIEW: _CaseSpec(_HOLDING, "论点是否仍然成立？", HoldingConclusion, 3),
    CaseType.HOLDING_CATALYST_REVIEW: _CaseSpec(
        _HOLDING, "催化剂兑现了吗？论点是否仍然成立？", HoldingConclusion, 3
    ),
    CaseType.HOLDING_FOLLOW_UP: _CaseSpec(_HOLDING, None, HoldingConclusion, 3),
    CaseType.WATCH_EVENT: _CaseSpec(_WATCH, "入场机会是否出现？", WatchConclusion, 4),
    CaseType.WATCH_REVIEW: _CaseSpec(_WATCH, "是否还值得关注？", WatchConclusion, 5),
    CaseType.WATCH_CATALYST_REVIEW: _CaseSpec(
        _WATCH, "催化剂兑现了吗？是否还值得关注？", WatchConclusion, 5
    ),
    CaseType.WATCH_FOLLOW_UP: _CaseSpec(_WATCH, None, WatchConclusion, 5),
    CaseType.CANDIDATE_EVENT: _CaseSpec(_CANDIDATE, "是否值得升级为关注？", CandidateConclusion, 6),
    CaseType.CANDIDATE_EVALUATION: _CaseSpec(_CANDIDATE, "是否升级？", CandidateConclusion, 7),
    CaseType.EXPLORATION: _CaseSpec(None, "补充哪些候选？", ExplorationConclusion, 8),
    CaseType.WEEKLY_RETROSPECTIVE: _CaseSpec(None, None, None, 9),
    CaseType.SELL_RETROSPECTIVE: _CaseSpec(None, None, None, 9),
}

# 定时心跳的检查项开出的案件类型；针对一个状态的带股票，寻股和每周复盘不带
_HEARTBEAT_TYPES: Final = frozenset(
    {
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.WATCH_REVIEW,
        CaseType.WATCH_CATALYST_REVIEW,
        CaseType.CANDIDATE_EVALUATION,
        CaseType.EXPLORATION,
        CaseType.WEEKLY_RETROSPECTIVE,
    }
)
_EVENT_TYPES: Final = {
    _HOLDING: CaseType.HOLDING_EVENT,
    _WATCH: CaseType.WATCH_EVENT,
    _CANDIDATE: CaseType.CANDIDATE_EVENT,
}
_FOLLOW_UP_TYPES: Final = {
    _HOLDING: CaseType.HOLDING_FOLLOW_UP,
    _WATCH: CaseType.WATCH_FOLLOW_UP,
}

# spec「事件要求的子 agent」的表：事件案件至少向这个子 agent 派过任务
_REQUIRED_AGENTS: Final = {
    EventCategory.PRICE_VOLUME: SubAgent.QUANT,
    EventCategory.NEWS: SubAgent.NEWS,
    EventCategory.FILING: SubAgent.FUNDAMENTALS,
    EventCategory.EARNINGS: SubAgent.FUNDAMENTALS,
    EventCategory.ENTRY: SubAgent.QUANT,
    EventCategory.THESIS_CONDITION: SubAgent.QUANT,
}


@dataclass(frozen=True, slots=True)
class EventTrigger:
    """事件。holding_risk 是事件检测标注的持仓风险级别，只对持仓有效。"""

    symbol: str
    category: EventCategory
    holding_risk: bool


@dataclass(frozen=True, slots=True)
class HeartbeatTrigger:
    """定时心跳的检查项，直接带它开出的案件类型：检查项和案件类型一一对应。

    针对一个状态的检查项带股票，寻股和每周复盘不带；不符合时抛 ValueError。
    """

    case_type: CaseType
    symbol: str | None = None

    def __post_init__(self) -> None:
        if self.case_type not in _HEARTBEAT_TYPES:
            raise ValueError(f"定时心跳开不出案件类型 {self.case_type}")
        if (_CASE_TYPES[self.case_type].status is not None) != (self.symbol is not None):
            raise ValueError(f"定时心跳开出的 {self.case_type} 带不带股票和检查项不符")


@dataclass(frozen=True, slots=True)
class FollowUpTrigger:
    """获批的跟进到了触发时间。不带申请时的状态：按开始运行时的状态定类型。"""

    symbol: str
    question: str  # 申请时写下的问题


@dataclass(frozen=True, slots=True)
class SellTrigger:
    """卖出成交：止损单成交、紧急风险退出、PM 发起的减仓和清仓都算。"""

    symbol: str


type Trigger = EventTrigger | HeartbeatTrigger | FollowUpTrigger | SellTrigger


@dataclass(frozen=True, slots=True)
class CaseDefinition:
    """开始运行时确定的案件类型，以及它固定的问题、优先级和事件要求的子 agent。"""

    case_type: CaseType
    symbol: str | None  # 寻股和每周复盘没有
    question: str | None  # 复盘案件不唤醒 PM，没有问题
    priority: int  # 1 最高，9 最低
    required_agent: SubAgent | None  # 只有事件案件有


def _definition(
    case_type: CaseType,
    symbol: str | None,
    question: str | None = None,
    required_agent: SubAgent | None = None,
) -> CaseDefinition:
    """按表给出案件类型固定的内容；question 是跟进申请时写下的问题。"""
    spec = _CASE_TYPES[case_type]
    return CaseDefinition(
        case_type, symbol, question or spec.question, spec.priority, required_agent
    )


def define_case(trigger: Trigger, status: StockStatus | None) -> CaseDefinition | None:
    """按触发和股票现在的状态确定案件类型；None 表示案件不运行。

    status 是这只股票现在的状态，不在股票池也不是持仓时为 None；寻股和复盘不看它。
    """
    match trigger:
        case EventTrigger(symbol, category, holding_risk):
            if status is None:
                return None
            if status is _HOLDING and holding_risk:
                case_type = CaseType.HOLDING_RISK_EVENT
            else:
                case_type = _EVENT_TYPES[status]
            return _definition(case_type, symbol, required_agent=_REQUIRED_AGENTS[category])
        case HeartbeatTrigger(case_type, symbol):
            target = _CASE_TYPES[case_type].status
            if target is not None and status is not target:
                return None
            return _definition(case_type, symbol)
        case FollowUpTrigger(symbol, question):
            if status is None or status is _CANDIDATE:
                return None
            return _definition(_FOLLOW_UP_TYPES[status], symbol, question)
        case SellTrigger(symbol):
            return _definition(CaseType.SELL_RETROSPECTIVE, symbol)


def conclusion_model(case_type: CaseType) -> type[Conclusion] | None:
    """PM 要提交的结论种类；复盘案件不唤醒 PM，为 None。"""
    return _CASE_TYPES[case_type].conclusion
