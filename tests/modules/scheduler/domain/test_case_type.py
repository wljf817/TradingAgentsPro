"""触发和案件类型的测试。

场景见 openspec 的 case-types spec「触发」「案件类型」「开始运行时确定案件类型」
「事件要求的子 agent」。股票的状态按 spec 的基准数据：AAA 是候选，BBB 是关注，CCC 是持仓；
传入的状态是案件开始运行时的状态。
"""

from itertools import groupby

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    CandidateConclusion,
    ExplorationConclusion,
    HoldingConclusion,
    WatchConclusion,
)
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    EventTrigger,
    FollowUpTrigger,
    HeartbeatTrigger,
    SellTrigger,
    Trigger,
    conclusion_model,
    define_case,
)

HOLDING, WATCH, CANDIDATE = StockStatus.HOLDING, StockStatus.WATCH, StockStatus.CANDIDATE
NEWS, QUANT, FUNDAMENTALS = SubAgent.NEWS, SubAgent.QUANT, SubAgent.FUNDAMENTALS
EVENT_QUESTION = "这个事件是否削弱或推翻了论点？"
HOLDING_FOLLOW_UP_QUESTION = "大单公布 3 小时后盘中反应如何？"
WATCH_FOLLOW_UP_QUESTION = "12 月行业大会之后市场反应如何？"


# 触发


@pytest.mark.parametrize(
    ("case_type", "symbol"),
    [
        (CaseType.HOLDING_EVENT, "CCC"),
        (CaseType.HOLDING_REVIEW, None),
        (CaseType.EXPLORATION, "AAA"),
    ],
    ids=["案件类型是持仓事件", "持仓复查不带股票", "寻股带了股票"],
)
def test_invalid_trigger(case_type: CaseType, symbol: str | None) -> None:
    """spec「不合法的触发」。"""
    with pytest.raises(ValueError, match="定时心跳"):
        HeartbeatTrigger(case_type, symbol)


# 案件类型：每种类型一行，触发和状态见 spec「案件类型」的各个场景

_EVERY_TYPE: list[tuple[str, Trigger, StockStatus | None, CaseDefinition, type[object] | None]] = [
    (
        "持仓风险事件",
        EventTrigger("CCC", EventCategory.PRICE_VOLUME, holding_risk=True),
        HOLDING,
        CaseDefinition(CaseType.HOLDING_RISK_EVENT, "CCC", EVENT_QUESTION, 1, QUANT),
        HoldingConclusion,
    ),
    (
        "持仓事件",
        EventTrigger("CCC", EventCategory.NEWS, holding_risk=False),
        HOLDING,
        CaseDefinition(CaseType.HOLDING_EVENT, "CCC", EVENT_QUESTION, 2, NEWS),
        HoldingConclusion,
    ),
    (
        "持仓复查",
        HeartbeatTrigger(CaseType.HOLDING_REVIEW, "CCC"),
        HOLDING,
        CaseDefinition(CaseType.HOLDING_REVIEW, "CCC", "论点是否仍然成立？", 3, None),
        HoldingConclusion,
    ),
    (
        "持仓催化剂复查",
        HeartbeatTrigger(CaseType.HOLDING_CATALYST_REVIEW, "CCC"),
        HOLDING,
        CaseDefinition(
            CaseType.HOLDING_CATALYST_REVIEW,
            "CCC",
            "催化剂兑现了吗？论点是否仍然成立？",
            3,
            None,
        ),
        HoldingConclusion,
    ),
    (
        "持仓跟进",
        FollowUpTrigger("CCC", HOLDING_FOLLOW_UP_QUESTION),
        HOLDING,
        CaseDefinition(CaseType.HOLDING_FOLLOW_UP, "CCC", HOLDING_FOLLOW_UP_QUESTION, 3, None),
        HoldingConclusion,
    ),
    (
        "关注股事件",
        EventTrigger("BBB", EventCategory.EARNINGS, holding_risk=False),
        WATCH,
        CaseDefinition(CaseType.WATCH_EVENT, "BBB", "入场机会是否出现？", 4, FUNDAMENTALS),
        WatchConclusion,
    ),
    (
        "关注复查",
        HeartbeatTrigger(CaseType.WATCH_REVIEW, "BBB"),
        WATCH,
        CaseDefinition(CaseType.WATCH_REVIEW, "BBB", "是否还值得关注？", 5, None),
        WatchConclusion,
    ),
    (
        "关注催化剂复查",
        HeartbeatTrigger(CaseType.WATCH_CATALYST_REVIEW, "BBB"),
        WATCH,
        CaseDefinition(
            CaseType.WATCH_CATALYST_REVIEW, "BBB", "催化剂兑现了吗？是否还值得关注？", 5, None
        ),
        WatchConclusion,
    ),
    (
        "关注跟进",
        FollowUpTrigger("BBB", WATCH_FOLLOW_UP_QUESTION),
        WATCH,
        CaseDefinition(CaseType.WATCH_FOLLOW_UP, "BBB", WATCH_FOLLOW_UP_QUESTION, 5, None),
        WatchConclusion,
    ),
    (
        "候选股事件",
        EventTrigger("AAA", EventCategory.NEWS, holding_risk=False),
        CANDIDATE,
        CaseDefinition(CaseType.CANDIDATE_EVENT, "AAA", "是否值得升级为关注？", 6, NEWS),
        CandidateConclusion,
    ),
    (
        "候选评估",
        HeartbeatTrigger(CaseType.CANDIDATE_EVALUATION, "AAA"),
        CANDIDATE,
        CaseDefinition(CaseType.CANDIDATE_EVALUATION, "AAA", "是否升级？", 7, None),
        CandidateConclusion,
    ),
    (
        "寻股",
        HeartbeatTrigger(CaseType.EXPLORATION),
        None,
        CaseDefinition(CaseType.EXPLORATION, None, "补充哪些候选？", 8, None),
        ExplorationConclusion,
    ),
    (
        "每周复盘",
        HeartbeatTrigger(CaseType.WEEKLY_RETROSPECTIVE),
        None,
        CaseDefinition(CaseType.WEEKLY_RETROSPECTIVE, None, None, 9, None),
        None,
    ),
    (
        "卖出复盘",
        SellTrigger("CCC"),
        CANDIDATE,  # 止损单成交以后 CCC 是候选
        CaseDefinition(CaseType.SELL_RETROSPECTIVE, "CCC", None, 9, None),
        None,
    ),
]


@pytest.mark.parametrize(
    ("trigger", "status", "expected", "model"),
    [pytest.param(*row, id=name) for name, *row in _EVERY_TYPE],
)
def test_case_types(
    trigger: Trigger,
    status: StockStatus | None,
    expected: CaseDefinition,
    model: type[object] | None,
) -> None:
    """spec「案件类型」的前十四个场景：类型、问题、优先级和 PM 提交的结论种类。"""
    case = define_case(trigger, status)

    assert case == expected
    assert conclusion_model(expected.case_type) is model


def test_priority_order() -> None:
    """spec「九档优先级的顺序」。"""
    cases = [define_case(trigger, status) for _, trigger, status, _, _ in _EVERY_TYPE]
    ordered = sorted((c for c in cases if c is not None), key=lambda c: c.priority)

    groups = [{c.case_type for c in group} for _, group in groupby(ordered, lambda c: c.priority)]

    assert groups == [
        {CaseType.HOLDING_RISK_EVENT},
        {CaseType.HOLDING_EVENT},
        {CaseType.HOLDING_REVIEW, CaseType.HOLDING_CATALYST_REVIEW, CaseType.HOLDING_FOLLOW_UP},
        {CaseType.WATCH_EVENT},
        {CaseType.WATCH_REVIEW, CaseType.WATCH_CATALYST_REVIEW, CaseType.WATCH_FOLLOW_UP},
        {CaseType.CANDIDATE_EVENT},
        {CaseType.CANDIDATE_EVALUATION},
        {CaseType.EXPLORATION},
        {CaseType.WEEKLY_RETROSPECTIVE, CaseType.SELL_RETROSPECTIVE},
    ]
    assert sum(len(g) for g in groups) == len(CaseType)


# 开始运行时确定案件类型


def test_queued_event_runs_by_the_new_status_after_a_stop_fill() -> None:
    """spec「止损成交后排队的事件按新状态运行」：CCC 止损成交后变为候选。"""
    case = define_case(EventTrigger("CCC", EventCategory.NEWS, holding_risk=False), CANDIDATE)

    assert case is not None
    assert (case.case_type, case.priority) == (CaseType.CANDIDATE_EVENT, 6)


def test_holding_risk_only_counts_for_holdings() -> None:
    """spec「持仓风险级别只对持仓有效」。"""
    case = define_case(EventTrigger("BBB", EventCategory.PRICE_VOLUME, holding_risk=True), WATCH)

    assert case is not None
    assert (case.case_type, case.priority) == (CaseType.WATCH_EVENT, 4)


def test_event_of_a_stock_outside_the_pool_does_not_run() -> None:
    """spec「不在股票池的股票的事件不运行」：AAA 已经被删除。"""
    assert define_case(EventTrigger("AAA", EventCategory.NEWS, holding_risk=False), None) is None


@pytest.mark.parametrize(
    ("trigger", "status"),
    [
        (HeartbeatTrigger(CaseType.HOLDING_REVIEW, "CCC"), CANDIDATE),
        (HeartbeatTrigger(CaseType.WATCH_REVIEW, "BBB"), HOLDING),
        (HeartbeatTrigger(CaseType.WATCH_CATALYST_REVIEW, "BBB"), CANDIDATE),
        (HeartbeatTrigger(CaseType.HOLDING_CATALYST_REVIEW, "CCC"), None),
    ],
    ids=[
        "持仓复查时已是候选",
        "关注复查时已是持仓",
        "关注催化剂复查时已是候选",
        "持仓催化剂复查时已移出",
    ],
)
def test_review_no_longer_in_its_status(trigger: Trigger, status: StockStatus | None) -> None:
    """spec「复查案件开始时已经不是那个状态」。"""
    assert define_case(trigger, status) is None


@pytest.mark.parametrize("status", [WATCH, None], ids=["已经升级为关注", "已经被删除"])
def test_candidate_evaluation_no_longer_a_candidate(status: StockStatus | None) -> None:
    """spec「候选评估开始时已经不是候选」。"""
    assert define_case(HeartbeatTrigger(CaseType.CANDIDATE_EVALUATION, "AAA"), status) is None


def test_follow_up_after_a_watched_stock_is_bought_runs_as_a_holding() -> None:
    """spec「关注股买入以后的跟进按持仓运行」。"""
    case = define_case(FollowUpTrigger("BBB", WATCH_FOLLOW_UP_QUESTION), HOLDING)

    assert case == CaseDefinition(
        CaseType.HOLDING_FOLLOW_UP, "BBB", WATCH_FOLLOW_UP_QUESTION, 3, None
    )
    assert conclusion_model(CaseType.HOLDING_FOLLOW_UP) is HoldingConclusion


@pytest.mark.parametrize("status", [CANDIDATE, None], ids=["已经降为候选", "已经被删除"])
def test_follow_up_of_a_candidate_or_a_removed_stock(status: StockStatus | None) -> None:
    """spec「跟进开始时已经是候选或不在股票池」。"""
    assert define_case(FollowUpTrigger("BBB", WATCH_FOLLOW_UP_QUESTION), status) is None


@pytest.mark.parametrize("status", [*StockStatus, None])
def test_exploration_and_retrospectives_ignore_the_status(status: StockStatus | None) -> None:
    """spec「寻股和复盘不看状态」：CCC 由 PM 清仓后移出，状态是 None。"""
    exploration = define_case(HeartbeatTrigger(CaseType.EXPLORATION), status)
    weekly = define_case(HeartbeatTrigger(CaseType.WEEKLY_RETROSPECTIVE), status)
    sell = define_case(SellTrigger("CCC"), status)

    assert exploration is not None
    assert exploration.case_type is CaseType.EXPLORATION
    assert weekly is not None
    assert weekly.case_type is CaseType.WEEKLY_RETROSPECTIVE
    assert sell is not None
    assert sell.case_type is CaseType.SELL_RETROSPECTIVE


# 事件要求的子 agent


def _required_agent(trigger: Trigger, status: StockStatus | None) -> SubAgent | None:
    case = define_case(trigger, status)
    assert case is not None
    return case.required_agent


def test_news_event_requires_news() -> None:
    """spec「新闻事件至少派给 News」。"""
    trigger = EventTrigger("CCC", EventCategory.NEWS, holding_risk=False)

    assert _required_agent(trigger, HOLDING) is NEWS


@pytest.mark.parametrize("category", [EventCategory.FILING, EventCategory.EARNINGS])
def test_filing_and_earnings_require_fundamentals(category: EventCategory) -> None:
    """spec「公告和财报发布至少派给 Fundamentals」。"""
    trigger = EventTrigger("BBB", category, holding_risk=False)

    assert _required_agent(trigger, WATCH) is FUNDAMENTALS


@pytest.mark.parametrize(
    ("trigger", "status"),
    [
        (EventTrigger("AAA", EventCategory.PRICE_VOLUME, holding_risk=False), CANDIDATE),
        (EventTrigger("BBB", EventCategory.ENTRY, holding_risk=False), WATCH),
        (EventTrigger("CCC", EventCategory.THESIS_CONDITION, holding_risk=False), HOLDING),
    ],
    ids=["价格和成交量异动", "入场条件满足", "达到目标价或证伪条件触发"],
)
def test_price_events_require_quant(trigger: Trigger, status: StockStatus) -> None:
    """spec「价格类事件至少派给 Quant」。"""
    assert _required_agent(trigger, status) is QUANT


@pytest.mark.parametrize(
    ("trigger", "status"),
    [
        (HeartbeatTrigger(CaseType.HOLDING_REVIEW, "CCC"), HOLDING),
        (FollowUpTrigger("BBB", WATCH_FOLLOW_UP_QUESTION), WATCH),
        (HeartbeatTrigger(CaseType.CANDIDATE_EVALUATION, "AAA"), CANDIDATE),
        (HeartbeatTrigger(CaseType.EXPLORATION), None),
    ],
    ids=["持仓复查", "跟进", "候选评估", "寻股"],
)
def test_cases_other_than_events_require_no_agent(
    trigger: Trigger, status: StockStatus | None
) -> None:
    """spec「不是事件的案件没有这项要求」。"""
    assert _required_agent(trigger, status) is None


# 性质测试（design D11）

# spec「案件类型」的表：针对的状态（None 表示不针对单只股票或不看状态）和优先级
_TABLE: dict[CaseType, tuple[StockStatus | None, int]] = {
    CaseType.HOLDING_RISK_EVENT: (HOLDING, 1),
    CaseType.HOLDING_EVENT: (HOLDING, 2),
    CaseType.HOLDING_REVIEW: (HOLDING, 3),
    CaseType.HOLDING_CATALYST_REVIEW: (HOLDING, 3),
    CaseType.HOLDING_FOLLOW_UP: (HOLDING, 3),
    CaseType.WATCH_EVENT: (WATCH, 4),
    CaseType.WATCH_REVIEW: (WATCH, 5),
    CaseType.WATCH_CATALYST_REVIEW: (WATCH, 5),
    CaseType.WATCH_FOLLOW_UP: (WATCH, 5),
    CaseType.CANDIDATE_EVENT: (CANDIDATE, 6),
    CaseType.CANDIDATE_EVALUATION: (CANDIDATE, 7),
    CaseType.EXPLORATION: (None, 8),
    CaseType.WEEKLY_RETROSPECTIVE: (None, 9),
    CaseType.SELL_RETROSPECTIVE: (None, 9),
}
_STATUS_CONCLUSIONS: dict[StockStatus, type[object]] = {
    HOLDING: HoldingConclusion,
    WATCH: WatchConclusion,
    CANDIDATE: CandidateConclusion,
}
# 定时心跳能开的案件类型；针对一个状态的带股票
_HEARTBEAT_TYPES = (
    CaseType.HOLDING_REVIEW,
    CaseType.HOLDING_CATALYST_REVIEW,
    CaseType.WATCH_REVIEW,
    CaseType.WATCH_CATALYST_REVIEW,
    CaseType.CANDIDATE_EVALUATION,
    CaseType.EXPLORATION,
    CaseType.WEEKLY_RETROSPECTIVE,
)
_symbols = st.sampled_from(("AAA", "BBB", "CCC"))
_statuses = st.sampled_from((*StockStatus, None))


@st.composite
def _heartbeats(draw: st.DrawFn) -> HeartbeatTrigger:
    case_type = draw(st.sampled_from(_HEARTBEAT_TYPES))
    symbol = draw(_symbols) if _TABLE[case_type][0] is not None else None
    return HeartbeatTrigger(case_type, symbol)


_triggers: st.SearchStrategy[Trigger] = st.one_of(
    st.builds(EventTrigger, _symbols, st.sampled_from(EventCategory), st.booleans()),
    _heartbeats(),
    st.builds(FollowUpTrigger, _symbols, st.just("申请时写下的问题")),
    st.builds(SellTrigger, _symbols),
)


def _runs(trigger: Trigger, status: StockStatus | None) -> bool:
    """spec「开始运行时确定案件类型」的表的"不运行的情况"之外。"""
    match trigger:
        case EventTrigger():
            return status is not None
        case HeartbeatTrigger():
            target = _TABLE[trigger.case_type][0]
            return target is None or status is target
        case FollowUpTrigger():
            return status in (HOLDING, WATCH)
        case SellTrigger():
            return True


@given(trigger=_triggers, status=_statuses)
def test_case_type_follows_the_status_and_the_table(
    trigger: Trigger, status: StockStatus | None
) -> None:
    """任意触发和状态：针对单只股票的类型针对的就是传入的状态，优先级和 spec 的表一致，
    结论种类和状态一致；只在 spec 规定的情况下不运行。
    """
    case = define_case(trigger, status)

    assert (case is not None) == _runs(trigger, status)
    if case is None:
        return
    target, priority = _TABLE[case.case_type]
    assert case.priority == priority
    assert case.symbol == getattr(trigger, "symbol", None)
    if target is not None:
        assert status is target
        assert conclusion_model(case.case_type) is _STATUS_CONCLUSIONS[target]
