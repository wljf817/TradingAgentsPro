"""结论校验的测试。场景见 openspec 的 case-types spec「结论的校验」和「研究要求的配置」。

基准数据见 tests/modules/scheduler/builders.py：本案件派过任务的子 agent 是 News 和 Quant，
状态是完成的 Result 是 R-1（News）和 R-2（Quant）。spec 没有指定事件类别的事件案件，用要求的
子 agent 在派过任务之列的类别：持仓事件是 CCC 的新闻事件（News），关注股事件是 BBB 的入场条件
满足事件（Quant），候选股事件是 AAA 的新闻事件（News）。
"""

from collections.abc import Callable, Collection, Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.modules.scheduler.builders import (
    CLOSE_PLAN,
    COMPLETED,
    DISPATCHED,
    FOLLOW_UP,
    INVESTMENT_THESIS,
    PLAN,
    REDUCE_PLAN,
    SIGNAL,
    WATCH_THESIS,
    candidate,
    exploration,
    holding,
    watch,
)
from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    AfterClose,
    Conclusion,
    Decision,
    PoolAddition,
)
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    ConclusionRejection,
    EventTrigger,
    FollowUpTrigger,
    HeartbeatTrigger,
    SchedulerConfig,
    SellTrigger,
    Trigger,
    conclusion_rejection,
    define_case,
)

HOLDING, WATCH, CANDIDATE = StockStatus.HOLDING, StockStatus.WATCH, StockStatus.CANDIDATE
NEWS, QUANT, FUNDAMENTALS = SubAgent.NEWS, SubAgent.QUANT, SubAgent.FUNDAMENTALS
WRONG_KIND = ConclusionRejection.WRONG_KIND
FOLLOW_UP_IN_FOLLOW_UP = ConclusionRejection.FOLLOW_UP_IN_FOLLOW_UP
MISSING_RENEWAL = ConclusionRejection.MISSING_RENEWAL
MISSING_REQUIRED_AGENT = ConclusionRejection.MISSING_REQUIRED_AGENT
INSUFFICIENT_RESEARCH = ConclusionRejection.INSUFFICIENT_RESEARCH
CONFIG = SchedulerConfig()


def _case(trigger: Trigger, status: StockStatus | None) -> CaseDefinition:
    case = define_case(trigger, status)
    assert case is not None
    return case


def _event(symbol: str, category: EventCategory, status: StockStatus) -> CaseDefinition:
    return _case(EventTrigger(symbol, category, holding_risk=False), status)


HOLDING_RISK_EVENT = _case(EventTrigger("CCC", EventCategory.PRICE_VOLUME, True), HOLDING)
HOLDING_EVENT = _event("CCC", EventCategory.NEWS, HOLDING)
HOLDING_REVIEW = _case(HeartbeatTrigger(CaseType.HOLDING_REVIEW, "CCC"), HOLDING)
HOLDING_CATALYST_REVIEW = _case(HeartbeatTrigger(CaseType.HOLDING_CATALYST_REVIEW, "CCC"), HOLDING)
HOLDING_FOLLOW_UP = _case(FollowUpTrigger("CCC", "大单公布 3 小时后盘中反应如何？"), HOLDING)
WATCH_EVENT = _event("BBB", EventCategory.ENTRY, WATCH)
WATCH_REVIEW = _case(HeartbeatTrigger(CaseType.WATCH_REVIEW, "BBB"), WATCH)
WATCH_CATALYST_REVIEW = _case(HeartbeatTrigger(CaseType.WATCH_CATALYST_REVIEW, "BBB"), WATCH)
WATCH_FOLLOW_UP = _case(FollowUpTrigger("BBB", "12 月行业大会之后市场反应如何？"), WATCH)
CANDIDATE_EVENT = _event("AAA", EventCategory.NEWS, CANDIDATE)
CANDIDATE_EVALUATION = _case(HeartbeatTrigger(CaseType.CANDIDATE_EVALUATION, "AAA"), CANDIDATE)
EXPLORATION = _case(HeartbeatTrigger(CaseType.EXPLORATION), None)
WEEKLY_RETROSPECTIVE = _case(HeartbeatTrigger(CaseType.WEEKLY_RETROSPECTIVE), None)
SELL_RETROSPECTIVE = _case(SellTrigger("CCC"), CANDIDATE)


def _rejection(
    case: CaseDefinition,
    conclusion: Conclusion,
    dispatched: Collection[SubAgent] = DISPATCHED,
    completed: Mapping[str, SubAgent] = COMPLETED,
    config: SchedulerConfig = CONFIG,
) -> ConclusionRejection | None:
    """基准数据下校验结论；参数覆盖派过任务的子 agent、已完成的 Result 和配置。"""
    return conclusion_rejection(case, conclusion, dispatched, completed, config)


def _buy(*cited: str) -> Conclusion:
    """关注的结论：开仓方案，基准方案条款、基准投资论点，引用 cited。"""
    return watch(Decision.BUY, plan=PLAN, thesis=INVESTMENT_THESIS, cited_results=cited)


def _add(*cited: str) -> Conclusion:
    """持仓的结论：加仓，基准方案条款，引用 cited。"""
    return holding(Decision.ADD, plan=PLAN, cited_results=cited)


def _promote(*cited: str) -> Conclusion:
    """候选的结论：升级，基准关注论点，引用 cited。"""
    return candidate(Decision.PROMOTE, watch_thesis=WATCH_THESIS, cited_results=cited)


@pytest.mark.parametrize(
    ("case", "conclusion"),
    [
        (HOLDING_RISK_EVENT, watch(Decision.KEEP_WATCHING)),
        (EXPLORATION, candidate(Decision.KEEP)),
        (CANDIDATE_EVALUATION, exploration()),
    ],
    ids=["持仓风险事件提交关注的结论", "寻股提交候选的结论", "候选评估提交寻股的结论"],
)
def test_conclusion_of_the_wrong_kind(case: CaseDefinition, conclusion: Conclusion) -> None:
    """spec「结论不属于这个案件」：被拒绝。"""
    assert _rejection(case, conclusion) is WRONG_KIND


def test_conclusion_of_the_right_kind() -> None:
    """spec「结论不属于这个案件」：关注跟进提交关注的结论（继续关注）通过。"""
    assert _rejection(WATCH_FOLLOW_UP, watch(Decision.KEEP_WATCHING)) is None


def test_follow_up_cases_cannot_request_another_follow_up() -> None:
    """spec「跟进案件不能再申请跟进」。"""
    hold = holding(Decision.HOLD, follow_up=FOLLOW_UP)
    keep_watching = watch(Decision.KEEP_WATCHING, follow_up=FOLLOW_UP)

    assert _rejection(HOLDING_FOLLOW_UP, hold) is FOLLOW_UP_IN_FOLLOW_UP
    assert _rejection(WATCH_FOLLOW_UP, keep_watching) is FOLLOW_UP_IN_FOLLOW_UP
    assert _rejection(HOLDING_EVENT, hold) is None


@pytest.mark.parametrize(
    ("case", "conclusion", "rejection"),
    [
        (HOLDING_REVIEW, holding(Decision.HOLD), MISSING_RENEWAL),
        (HOLDING_REVIEW, holding(Decision.HOLD, thesis=INVESTMENT_THESIS), None),
        (HOLDING_CATALYST_REVIEW, holding(Decision.REDUCE, plan=REDUCE_PLAN), MISSING_RENEWAL),
        (WATCH_CATALYST_REVIEW, watch(Decision.KEEP_WATCHING), MISSING_RENEWAL),
        (WATCH_REVIEW, watch(Decision.KEEP_WATCHING, watch_thesis=WATCH_THESIS), None),
    ],
    ids=[
        "持仓复查持有不附",
        "持仓复查持有附了",
        "持仓催化剂复查减仓不附",
        "关注催化剂复查继续关注不附",
        "关注复查继续关注附了",
    ],
)
def test_review_renewal_needs_a_thesis_update(
    case: CaseDefinition, conclusion: Conclusion, rejection: ConclusionRejection | None
) -> None:
    """spec「复查案件续期要附论点更新」。"""
    assert _rejection(case, conclusion) is rejection


@pytest.mark.parametrize(
    ("case", "conclusion"),
    [
        (HOLDING_REVIEW, holding(Decision.CLOSE, plan=CLOSE_PLAN, after_close=AfterClose.REMOVE)),
        (WATCH_REVIEW, watch(Decision.DEMOTE)),
        (WATCH_REVIEW, _buy("R-1", "R-2")),
    ],
    ids=["持仓复查清仓", "关注复查降为候选", "关注复查开仓方案"],
)
def test_decisions_that_do_not_renew(case: CaseDefinition, conclusion: Conclusion) -> None:
    """spec「不续期的决定不用附论点更新」。"""
    assert _rejection(case, conclusion) is None


@pytest.mark.parametrize("case", [HOLDING_EVENT, HOLDING_FOLLOW_UP], ids=["持仓事件", "持仓跟进"])
def test_cases_other_than_reviews_need_no_renewal(case: CaseDefinition) -> None:
    """spec「不是复查类案件不要求续期」。"""
    assert _rejection(case, holding(Decision.HOLD)) is None


def test_missing_the_agent_the_event_requires() -> None:
    """spec「缺少事件要求的子 agent」：BBB 的公告事件要求 Fundamentals。"""
    case = _event("BBB", EventCategory.FILING, WATCH)
    keep_watching = watch(Decision.KEEP_WATCHING)

    assert _rejection(case, keep_watching) is MISSING_REQUIRED_AGENT
    # 还向 Fundamentals 派过任务，它的 Result 是超时，不在已完成的 Result 里
    assert _rejection(case, keep_watching, dispatched={NEWS, QUANT, FUNDAMENTALS}) is None


def test_cases_other_than_events_need_no_dispatch() -> None:
    """spec「非事件案件不要求派任务」。"""
    keep = candidate(Decision.KEEP, cited_results=())

    assert _rejection(CANDIDATE_EVALUATION, keep, dispatched=(), completed={}) is None


@pytest.mark.parametrize(
    ("cited", "rejection"),
    [
        (("R-1", "R-2"), None),
        (("R-1",), INSUFFICIENT_RESEARCH),
        (("R-1", "R-3"), INSUFFICIENT_RESEARCH),
    ],
    ids=["R-1 和 R-2", "只引用 R-1", "R-1 和 R-3 都出自 News"],
)
def test_research_boundary(cited: tuple[str, ...], rejection: ConclusionRejection | None) -> None:
    """spec「研究要求的边界」：R-3 也出自 News，状态是完成。"""
    completed = COMPLETED | {"R-3": NEWS}

    assert _rejection(WATCH_EVENT, _buy(*cited), completed=completed) is rejection


def test_results_that_are_not_completed_do_not_count() -> None:
    """spec「状态不是完成的 Result 不算」：R-4 出自 Fundamentals，无信号，没有传入。"""
    assert _rejection(WATCH_EVENT, _buy("R-1", "R-4")) is INSUFFICIENT_RESEARCH


def test_add_and_promote_have_research_requirements_too() -> None:
    """spec「加仓和升级也有研究要求」。"""
    assert _rejection(HOLDING_EVENT, _add("R-1")) is INSUFFICIENT_RESEARCH
    assert _rejection(CANDIDATE_EVENT, _promote("R-2")) is INSUFFICIENT_RESEARCH
    assert _rejection(HOLDING_EVENT, _add("R-1", "R-2")) is None
    assert _rejection(CANDIDATE_EVENT, _promote("R-1", "R-2")) is None


@pytest.mark.parametrize(
    ("case", "conclusion"),
    [
        (HOLDING_EVENT, holding(Decision.HOLD, cited_results=())),
        (HOLDING_EVENT, holding(Decision.REDUCE, plan=REDUCE_PLAN, cited_results=())),
        (
            HOLDING_EVENT,
            holding(
                Decision.CLOSE, plan=CLOSE_PLAN, after_close=AfterClose.REMOVE, cited_results=()
            ),
        ),
        (CANDIDATE_EVALUATION, candidate(Decision.KEEP, cited_results=())),
        (CANDIDATE_EVALUATION, candidate(Decision.REMOVE, cited_results=())),
        (
            EXPLORATION,
            exploration(
                additions=(
                    PoolAddition(
                        symbol="DDD", reason="放量突破，论点草稿：订单回暖", signal=SIGNAL
                    ),
                ),
                cited_results=(),
            ),
        ),
    ],
    ids=["持有", "减仓", "清仓", "保持", "移出", "寻股加入 DDD"],
)
def test_other_decisions_have_no_research_requirement(
    case: CaseDefinition, conclusion: Conclusion
) -> None:
    """spec「其他决定没有研究要求」。"""
    assert _rejection(case, conclusion) is None


def test_the_first_failing_check_is_given() -> None:
    """spec「多项不通过时给出第一项」。"""
    add_without_renewal = holding(Decision.ADD, plan=PLAN, cited_results=("R-1",))
    news_event = _event("BBB", EventCategory.NEWS, WATCH)
    keep_watching_with_follow_up = watch(Decision.KEEP_WATCHING, follow_up=FOLLOW_UP)

    assert _rejection(HOLDING_REVIEW, add_without_renewal) is MISSING_RENEWAL
    assert (
        _rejection(news_event, _buy("R-2"), dispatched={QUANT}, completed={"R-2": QUANT})
        is MISSING_REQUIRED_AGENT
    )
    assert _rejection(HOLDING_FOLLOW_UP, keep_watching_with_follow_up) is WRONG_KIND


@pytest.mark.parametrize("case", [WEEKLY_RETROSPECTIVE, SELL_RETROSPECTIVE], ids=["每周", "卖出"])
@pytest.mark.parametrize(
    "conclusion",
    [
        holding(Decision.HOLD),
        watch(Decision.KEEP_WATCHING),
        candidate(Decision.KEEP),
        exploration(),
    ],
    ids=["持仓", "关注", "候选", "寻股"],
)
def test_retrospectives_have_no_conclusion(case: CaseDefinition, conclusion: Conclusion) -> None:
    """spec「复盘案件没有结论」。"""
    with pytest.raises(ValueError, match="复盘"):
        _rejection(case, conclusion)


# 研究要求的配置


def test_research_requirement_follows_the_config() -> None:
    """spec「改成 1 个或 3 个」。"""
    one = SchedulerConfig(min_cited_agents=1)
    three = SchedulerConfig(min_cited_agents=3)

    assert _rejection(WATCH_EVENT, _buy("R-1"), config=one) is None
    assert _rejection(WATCH_EVENT, _buy("R-1", "R-2"), config=three) is INSUFFICIENT_RESEARCH


# 性质测试（design D11）

# 有研究要求的决定，各配一个不看续期和跟进的事件案件；派过任务的子 agent 是全部三个
_RESEARCHED: dict[str, tuple[CaseDefinition, Callable[..., Conclusion]]] = {
    "开仓方案": (WATCH_EVENT, _buy),
    "加仓": (HOLDING_EVENT, _add),
    "升级": (CANDIDATE_EVENT, _promote),
}
_ALL_AGENTS = frozenset(SubAgent)
_agents = st.sampled_from(SubAgent)


@st.composite
def _passing_citations(draw: st.DrawFn) -> tuple[list[str], dict[str, SubAgent], int]:
    """通过研究要求的引用：引用的 Result ID、已完成的 Result 出自哪个子 agent、研究要求的
    子 agent 数。

    引用里至少有一个已完成的 Result，也可能有没完成的（不在已完成的 Result 里）；已完成的 Result
    也可能没被引用。研究要求的子 agent 数不超过引用的已完成 Result 来自的子 agent 数。
    """
    cited_agents = draw(st.lists(_agents, min_size=1, max_size=4))
    completed = {f"R-{n}": agent for n, agent in enumerate(cited_agents, 1)}
    cited = [*completed, *(f"P-{n}" for n in range(draw(st.integers(0, 2))))]
    uncited = draw(st.lists(_agents, max_size=3))
    completed |= {f"U-{n}": agent for n, agent in enumerate(uncited, 1)}
    min_cited_agents = draw(st.integers(1, len(set(cited_agents))))
    return draw(st.permutations(cited)), completed, min_cited_agents


@given(
    decision=st.sampled_from(sorted(_RESEARCHED)),
    citations=_passing_citations(),
    data=st.data(),
)
def test_citing_one_more_completed_result_never_makes_research_insufficient(
    decision: str, citations: tuple[list[str], dict[str, SubAgent], int], data: st.DataObject
) -> None:
    """在一份通过研究要求的结论上多引用一个已完成的 Result，不会变成"研究不足"。

    多引用的是已完成但还没引用的 Result，或者一个新完成的 Result。
    """
    case, conclusion = _RESEARCHED[decision]
    cited, completed, min_cited_agents = citations
    config = SchedulerConfig(min_cited_agents=min_cited_agents)
    assert _rejection(case, conclusion(*cited), _ALL_AGENTS, completed, config) is None
    extra_id, agent = data.draw(
        st.sampled_from(sorted(completed.items())) | st.tuples(st.just("N-1"), _agents)
    )

    after = _rejection(
        case, conclusion(*cited, extra_id), _ALL_AGENTS, completed | {extra_id: agent}, config
    )

    assert after is None
