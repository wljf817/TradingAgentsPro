"""PM 结论格式的测试：四种结论、PM 写的交易方案和跟进申请。

场景见 openspec 的 case-types spec「结论的公共部分」「持仓的结论」「关注的结论」「候选的结论」
「寻股的结论」「PM 写的交易方案」「跟进申请的格式」。基准数据和 spec 一致。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from tradingagentspro.contracts.conclusion import (
    AfterClose,
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
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan

HOLD, ADD, REDUCE, CLOSE = Decision.HOLD, Decision.ADD, Decision.REDUCE, Decision.CLOSE
BUY, KEEP_WATCHING, DEMOTE, REMOVE = (
    Decision.BUY,
    Decision.KEEP_WATCHING,
    Decision.DEMOTE,
    Decision.REMOVE,
)
PROMOTE, KEEP = Decision.PROMOTE, Decision.KEEP
LIMIT, MARKET = OrderType.LIMIT, OrderType.MARKET

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
WATCH_THESIS = WatchThesis(
    rationale="新品周期带动收入加速",
    entry_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(50)),
    ),
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    invalidation="毛利率连续两个季度下滑",
    review_day=date(2026, 10, 21),
)
TRIGGER_AT = datetime(2026, 11, 19, 14, 30, tzinfo=UTC)


def _follow_up_fields(**changes: Any) -> dict[str, Any]:
    """基准跟进申请的字段，changes 覆盖其中的字段。"""
    fields = {
        "reason": FollowUpReason.EXTERNAL_SCHEDULE,
        "reference": {"kind": ReferenceKind.SCHEDULE, "ref_id": "S-9"},
        "question": "竞争对手 11 月 18 日发财报，之后对这只股票有什么影响？",
        "possible_actions": "减仓",
        "trigger_at": TRIGGER_AT,
    }
    return fields | changes


FOLLOW_UP = FollowUpRequest.model_validate(_follow_up_fields())


def _plan(**changes: Any) -> dict[str, Any]:
    """基准方案条款：100 股，限价单，限价 50，止损价 45，紧急阈值 0.08；changes 覆盖其中的字段，
    值为 None 表示不写。
    """
    fields = {
        "quantity": 100,
        "order_type": LIMIT,
        "limit_price": Decimal(50),
        "stop_price": Decimal(45),
        "emergency_threshold": Decimal("0.08"),
    }
    return fields | changes


def _market_plan(quantity: int | None) -> dict[str, Any]:
    """市价单的退出方案：减仓写股数，清仓不写。"""
    return {"quantity": quantity, "order_type": MARKET}


REDUCE_PLAN = _market_plan(60)
CLOSE_PLAN = _market_plan(None)


def _conclusion(decision: Decision, **changes: Any) -> dict[str, Any]:
    """结论的公共部分加上决定，changes 覆盖或补充其中的字段。"""
    fields = {
        "answer": "论点仍然成立",
        "rationale": "订单和毛利率都符合预期",
        "score": 1,
        "cited_results": ("R-1", "R-2"),
        "decision": decision,
    }
    return fields | changes


def _buy(plan: dict[str, Any]) -> dict[str, Any]:
    """决定开仓方案，交易方案是 plan，附基准投资论点。"""
    return _conclusion(BUY, plan=plan, thesis=INVESTMENT_THESIS)


def _close(plan: dict[str, Any]) -> dict[str, Any]:
    """决定清仓，交易方案是 plan，去向是移出。"""
    return _conclusion(CLOSE, plan=plan, after_close=AfterClose.REMOVE)


def _assert_format_error(model: type[BaseModel], fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(fields)


def _format_errors(model: type[BaseModel], fields: dict[str, Any]) -> list[str]:
    """构造报的全部格式错误的说明。"""
    with pytest.raises(ValidationError) as raised:
        model.model_validate(fields)
    return [str(error["msg"]) for error in raised.value.errors()]


# 结论的公共部分


def test_complete_common_part() -> None:
    """spec「完整的公共部分」。"""
    conclusion = HoldingConclusion(
        answer="论点仍然成立",
        rationale="订单和毛利率都符合预期",
        score=1,
        cited_results=("R-1", "R-2"),
        decision=Decision.HOLD,
    )
    without_results = HoldingConclusion.model_validate(_conclusion(HOLD, cited_results=()))

    assert conclusion == HoldingConclusion.model_validate(_conclusion(HOLD))
    assert conclusion.decision is HOLD
    assert without_results.cited_results == ()


@pytest.mark.parametrize("changes", [{"answer": "  "}, {"rationale": ""}])
def test_empty_answer_or_rationale(changes: dict[str, Any]) -> None:
    """spec「回答或理由为空」。"""
    _assert_format_error(HoldingConclusion, _conclusion(HOLD, **changes))


@pytest.mark.parametrize("score", [-2, 2])
def test_score_bounds_inside(score: int) -> None:
    """spec「评分的边界」：-2 和 2 格式正确。"""
    assert HoldingConclusion.model_validate(_conclusion(HOLD, score=score)).score == score


@pytest.mark.parametrize("score", [-3, 3, 1.5, None], ids=["-3", "3", "1.5", "不写评分"])
def test_score_bounds_outside(score: float | None) -> None:
    """spec「评分的边界」：超出范围、不是整数或者不写评分都是格式错误。"""
    fields = _conclusion(HOLD, score=score)
    if score is None:
        del fields["score"]

    _assert_format_error(HoldingConclusion, fields)


def test_text_is_stripped() -> None:
    conclusion = HoldingConclusion.model_validate(_conclusion(HOLD, answer=" 论点仍然成立  "))

    assert conclusion.answer == "论点仍然成立"


def test_fields_outside_the_spec_are_rejected() -> None:
    _assert_format_error(HoldingConclusion, _conclusion(HOLD, note="补充说明"))


# 持仓的结论


def test_holding_hold() -> None:
    """spec「持有」。"""
    bare = HoldingConclusion.model_validate(_conclusion(HOLD))
    attached = HoldingConclusion.model_validate(
        _conclusion(HOLD, thesis=INVESTMENT_THESIS, follow_up=FOLLOW_UP)
    )

    assert (bare.plan, bare.thesis, bare.follow_up, bare.after_close) == (None, None, None, None)
    assert (attached.thesis, attached.follow_up) == (INVESTMENT_THESIS, FOLLOW_UP)
    _assert_format_error(HoldingConclusion, _conclusion(HOLD, plan=_plan()))


def test_holding_add() -> None:
    """spec「加仓」。"""
    conclusion = HoldingConclusion.model_validate(
        _conclusion(ADD, plan=_plan(), thesis=INVESTMENT_THESIS)
    )

    assert conclusion.plan == PlanDraft.model_validate(_plan())
    assert conclusion.thesis == INVESTMENT_THESIS
    _assert_format_error(HoldingConclusion, _conclusion(ADD))


def test_holding_reduce() -> None:
    """spec「减仓」：60 股、市价单，附基准跟进申请。"""
    conclusion = HoldingConclusion.model_validate(
        _conclusion(REDUCE, plan=REDUCE_PLAN, follow_up=FOLLOW_UP)
    )

    assert conclusion.plan == PlanDraft(quantity=60, order_type=MARKET)
    assert conclusion.follow_up == FOLLOW_UP


def test_close_then_watch_or_remove() -> None:
    """spec「清仓后转为关注或移出」。"""
    to_watch = HoldingConclusion.model_validate(
        _conclusion(CLOSE, plan=CLOSE_PLAN, after_close=AfterClose.WATCH, watch_thesis=WATCH_THESIS)
    )
    removed = HoldingConclusion.model_validate(
        _conclusion(CLOSE, plan=CLOSE_PLAN, after_close=AfterClose.REMOVE)
    )

    assert (to_watch.after_close, to_watch.watch_thesis) == (AfterClose.WATCH, WATCH_THESIS)
    assert (removed.after_close, removed.watch_thesis) == (AfterClose.REMOVE, None)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"after_close": AfterClose.WATCH},
        {"after_close": AfterClose.REMOVE, "watch_thesis": WATCH_THESIS},
    ],
    ids=["不写去向", "转为关注不附关注论点", "移出附了关注论点"],
)
def test_close_must_choose_where_to_go(changes: dict[str, Any]) -> None:
    """spec「清仓必须选去向」。"""
    _assert_format_error(HoldingConclusion, _conclusion(CLOSE, plan=CLOSE_PLAN, **changes))


@pytest.mark.parametrize(
    "changes", [{"thesis": INVESTMENT_THESIS}, {"follow_up": FOLLOW_UP}], ids=["论点更新", "跟进"]
)
def test_close_cannot_attach_a_thesis_update_or_follow_up(changes: dict[str, Any]) -> None:
    """spec「清仓不能附论点更新和跟进申请」。"""
    fields = _conclusion(CLOSE, plan=CLOSE_PLAN, after_close=AfterClose.REMOVE, **changes)

    _assert_format_error(HoldingConclusion, fields)


@pytest.mark.parametrize(("decision", "plan"), [(HOLD, None), (REDUCE, REDUCE_PLAN)])
def test_only_close_chooses_where_to_go(decision: Decision, plan: dict[str, Any] | None) -> None:
    """spec「不是清仓不能写去向」。"""
    fields = _conclusion(decision, plan=plan, after_close=AfterClose.REMOVE)

    _assert_format_error(HoldingConclusion, fields)


@pytest.mark.parametrize("decision", [BUY, KEEP_WATCHING, PROMOTE, KEEP])
def test_holding_cannot_choose_other_decisions(decision: Decision) -> None:
    """spec「持仓不能选别的状态的决定」。"""
    _assert_format_error(HoldingConclusion, _conclusion(decision))


# 关注的结论


def test_watch_buy() -> None:
    """spec「开仓方案」：正确的开仓方案。"""
    conclusion = WatchConclusion.model_validate(_buy(_plan()))

    assert conclusion.plan == PlanDraft.model_validate(_plan())
    assert conclusion.thesis == INVESTMENT_THESIS


@pytest.mark.parametrize(
    "changes",
    [
        {"plan": _plan()},
        {"thesis": INVESTMENT_THESIS},
        {"plan": _plan(), "thesis": INVESTMENT_THESIS, "watch_thesis": WATCH_THESIS},
        {"plan": _plan(), "thesis": INVESTMENT_THESIS, "follow_up": FOLLOW_UP},
    ],
    ids=["不附投资论点", "不写交易方案", "附论点更新", "附跟进申请"],
)
def test_watch_buy_format_errors(changes: dict[str, Any]) -> None:
    """spec「开仓方案」：格式错误。"""
    _assert_format_error(WatchConclusion, _conclusion(BUY, **changes))


def test_watch_keep_watching() -> None:
    """spec「继续关注」。"""
    bare = WatchConclusion.model_validate(_conclusion(KEEP_WATCHING))
    attached = WatchConclusion.model_validate(
        _conclusion(KEEP_WATCHING, watch_thesis=WATCH_THESIS, follow_up=FOLLOW_UP)
    )

    assert (bare.plan, bare.thesis, bare.watch_thesis, bare.follow_up) == (None, None, None, None)
    assert (attached.watch_thesis, attached.follow_up) == (WATCH_THESIS, FOLLOW_UP)
    _assert_format_error(WatchConclusion, _conclusion(KEEP_WATCHING, plan=_plan()))


@pytest.mark.parametrize("decision", [DEMOTE, REMOVE])
def test_watch_demote_and_remove(decision: Decision) -> None:
    """spec「降为候选和移出」。"""
    assert WatchConclusion.model_validate(_conclusion(decision)).decision is decision
    _assert_format_error(WatchConclusion, _conclusion(decision, watch_thesis=WATCH_THESIS))
    _assert_format_error(WatchConclusion, _conclusion(decision, follow_up=FOLLOW_UP))


@pytest.mark.parametrize("decision", [HOLD, ADD, CLOSE, PROMOTE])
def test_watch_cannot_choose_other_decisions(decision: Decision) -> None:
    """spec「关注不能选别的状态的决定」。"""
    _assert_format_error(WatchConclusion, _conclusion(decision))


# 候选的结论


def test_candidate_promote() -> None:
    """spec「升级」。"""
    conclusion = CandidateConclusion.model_validate(_conclusion(PROMOTE, watch_thesis=WATCH_THESIS))

    assert conclusion.watch_thesis == WATCH_THESIS
    _assert_format_error(CandidateConclusion, _conclusion(PROMOTE))


@pytest.mark.parametrize("decision", [KEEP, REMOVE])
def test_candidate_keep_and_remove(decision: Decision) -> None:
    """spec「保持和移出」。"""
    assert CandidateConclusion.model_validate(_conclusion(decision)).watch_thesis is None
    _assert_format_error(CandidateConclusion, _conclusion(decision, watch_thesis=WATCH_THESIS))


@pytest.mark.parametrize("decision", [BUY, KEEP_WATCHING, DEMOTE, HOLD])
def test_candidate_cannot_choose_other_decisions(decision: Decision) -> None:
    """spec「候选不能选别的状态的决定」。"""
    _assert_format_error(CandidateConclusion, _conclusion(decision))


# 寻股的结论


def _exploration(**changes: Any) -> dict[str, Any]:
    """寻股的结论的公共部分（没有评分），changes 覆盖或补充其中的字段。"""
    fields = _conclusion(KEEP)
    del fields["score"], fields["decision"]
    return fields | changes


def _ddd(**changes: Any) -> dict[str, Any]:
    """加入 DDD：理由"放量突破，论点草稿：订单回暖"，评分 1。"""
    return {"symbol": "DDD", "reason": "放量突破，论点草稿：订单回暖", "score": 1} | changes


AAA_REMOVAL = {"symbol": "AAA", "reason": "论点不再成立"}


def test_exploration_adds_and_removes() -> None:
    """spec「增删股票池」。"""
    eee = {"symbol": "EEE", "reason": "盈利预期上调", "score": 2}

    conclusion = ExplorationConclusion.model_validate(
        _exploration(additions=(_ddd(), eee), removals=(AAA_REMOVAL,))
    )

    assert [(a.symbol, a.score) for a in conclusion.additions] == [("DDD", 1), ("EEE", 2)]
    assert [(r.symbol, r.reason) for r in conclusion.removals] == [("AAA", "论点不再成立")]


def test_exploration_without_changes() -> None:
    """spec「这次不增删」。"""
    conclusion = ExplorationConclusion.model_validate(_exploration())

    assert (conclusion.additions, conclusion.removals) == ((), ())


def test_exploration_has_no_score_of_its_own() -> None:
    """spec「结论的公共部分」：寻股的结论没有评分，每只加入的候选各有一个。"""
    _assert_format_error(ExplorationConclusion, _exploration(score=1))


@pytest.mark.parametrize("score", [None, 3], ids=["不写评分", "评分是 3"])
def test_each_addition_needs_a_score(score: int | None) -> None:
    """spec「加入的候选必须有评分」。"""
    addition = _ddd(score=score)
    if score is None:
        del addition["score"]

    _assert_format_error(ExplorationConclusion, _exploration(additions=(addition,)))


@pytest.mark.parametrize(
    "changes",
    [
        {"additions": (_ddd(reason=" "),)},
        {"removals": (AAA_REMOVAL | {"reason": ""},)},
    ],
    ids=["加入的理由是空白", "删除的理由为空"],
)
def test_additions_and_removals_need_reasons(changes: dict[str, Any]) -> None:
    """spec「增删要写理由」。"""
    _assert_format_error(ExplorationConclusion, _exploration(**changes))


# PM 写的交易方案


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (HoldingConclusion, _conclusion(ADD, plan=_plan(order_type=MARKET, limit_price=None))),
        (HoldingConclusion, _conclusion(REDUCE, plan=REDUCE_PLAN | {"stop_price": Decimal(45)})),
        (HoldingConclusion, _close(_market_plan(100))),
        (WatchConclusion, _buy(_plan(stop_price=None))),
    ],
    ids=["加仓用市价单", "减仓写了止损价", "清仓写了股数", "开仓方案不写止损价"],
)
def test_plan_fields_are_checked_by_action(model: type[BaseModel], fields: dict[str, Any]) -> None:
    """spec「按动作检查字段」。"""
    _assert_format_error(model, fields)


@pytest.mark.parametrize(
    ("changes", "name"),
    [({"limit_price": Decimal("50.005")}, "限价"), ({"stop_price": Decimal("44.999")}, "止损价")],
)
def test_plan_prices_follow_the_tick_size(changes: dict[str, Any], name: str) -> None:
    """spec「按动作检查字段」：不符合最小变动单位。"""
    fields = _buy(_plan(**changes))

    [message] = _format_errors(WatchConclusion, fields)

    assert name in message
    assert "最小变动单位" in message


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (WatchConclusion, _buy(_plan(emergency_threshold=None))),
        (HoldingConclusion, _conclusion(ADD, plan=_plan(emergency_threshold=None))),
    ],
    ids=["开仓方案", "加仓"],
)
def test_buy_and_add_need_an_emergency_threshold(
    model: type[BaseModel], fields: dict[str, Any]
) -> None:
    """spec「买入和加仓必须写紧急阈值」：开仓方案和加仓。"""
    _assert_format_error(model, fields)


def test_reduce_without_an_emergency_threshold() -> None:
    """spec「买入和加仓必须写紧急阈值」：减仓不写紧急阈值。"""
    plan = {"quantity": 60, "order_type": LIMIT, "limit_price": Decimal(52)}

    conclusion = HoldingConclusion.model_validate(_conclusion(REDUCE, plan=plan))

    assert conclusion.plan == PlanDraft(quantity=60, order_type=LIMIT, limit_price=Decimal(52))


def test_expiry_and_deviation_are_optional() -> None:
    """spec「有效期和允许价格偏离可以不写」。"""
    expires_at = datetime(2026, 10, 15, 20, tzinfo=UTC)
    written = _plan(expires_at=expires_at, max_price_deviation=Decimal("0.05"))

    with_both = WatchConclusion.model_validate(_buy(written))
    without = WatchConclusion.model_validate(_buy(_plan()))

    assert with_both.plan is not None
    assert (with_both.plan.expires_at, with_both.plan.max_price_deviation) == (
        expires_at,
        Decimal("0.05"),
    )
    assert without.plan is not None
    assert (without.plan.expires_at, without.plan.max_price_deviation) == (None, None)


def test_conclusions_become_trade_plans() -> None:
    """spec「变成交易方案」：CCC 加仓以 P-1，BBB 开仓方案以 P-2。"""
    p1, p2 = UUID(int=1), UUID(int=2)
    add = HoldingConclusion.model_validate(_conclusion(ADD, plan=_plan()))
    buy = WatchConclusion.model_validate(_buy(_plan()))

    assert add.trade_plan(p1, "CCC", "能源") == TradePlan(
        plan_id=p1,
        symbol="CCC",
        sector="能源",
        action=TradeAction.ADD,
        quantity=100,
        order_type=LIMIT,
        limit_price=Decimal(50),
        stop_price=Decimal(45),
        emergency_threshold=Decimal("0.08"),
    )
    assert buy.trade_plan(p2, "BBB", "医疗") == TradePlan(
        plan_id=p2,
        symbol="BBB",
        sector="医疗",
        action=TradeAction.BUY,
        quantity=100,
        order_type=LIMIT,
        limit_price=Decimal(50),
        stop_price=Decimal(45),
        emergency_threshold=Decimal("0.08"),
    )


@pytest.mark.parametrize(
    ("fields", "action"),
    [
        (_conclusion(REDUCE, plan=REDUCE_PLAN), TradeAction.REDUCE),
        (_close(CLOSE_PLAN), TradeAction.CLOSE),
    ],
)
def test_exit_conclusions_become_exit_plans(fields: dict[str, Any], action: TradeAction) -> None:
    plan = HoldingConclusion.model_validate(fields).trade_plan(UUID(int=3), "CCC", "能源")

    assert plan is not None
    assert (plan.action, plan.quantity, plan.order_type) == (
        action,
        fields["plan"]["quantity"],
        MARKET,
    )


def test_conclusion_without_a_plan() -> None:
    """spec「没有方案的结论」。"""
    hold = HoldingConclusion.model_validate(_conclusion(HOLD))
    keep_watching = WatchConclusion.model_validate(_conclusion(KEEP_WATCHING))

    assert hold.trade_plan(UUID(int=1), "CCC", "能源") is None
    assert keep_watching.trade_plan(UUID(int=2), "BBB", "医疗") is None


# 跟进申请的格式


def test_complete_follow_up_request() -> None:
    """spec「完整的跟进申请」。"""
    request = FollowUpRequest(
        reason=FollowUpReason.EXTERNAL_SCHEDULE,
        reference=CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="S-9"),
        question="竞争对手 11 月 18 日发财报，之后对这只股票有什么影响？",
        possible_actions="减仓",
        trigger_at=TRIGGER_AT,
    )

    assert request == FOLLOW_UP


@pytest.mark.parametrize(
    ("reason", "reference"),
    [
        (
            FollowUpReason.EVENT_REACTION,
            CatalystReference(kind=ReferenceKind.EVENT, ref_id="V-7"),
        ),
        (
            FollowUpReason.OBSERVATION,
            Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_LEAST, value=Decimal(55)),
        ),
        (FollowUpReason.EXTERNAL_SCHEDULE, None),
    ],
    ids=["引用事件", "引用条件", "不写引用的对象"],
)
def test_follow_up_references(
    reason: FollowUpReason, reference: CatalystReference | Condition | None
) -> None:
    """spec「引用的对象」：格式正确。引用从字典解析，确认联合类型分得清两种引用。"""
    dumped = None if reference is None else reference.model_dump()

    request = FollowUpRequest.model_validate(_follow_up_fields(reason=reason, reference=dumped))

    assert request.reference == reference


def test_follow_up_event_reference_without_an_id() -> None:
    """spec「引用的对象」：引用事件，ID 为空。"""
    reference = {"kind": ReferenceKind.EVENT, "ref_id": ""}

    _assert_format_error(FollowUpRequest, _follow_up_fields(reference=reference))


@pytest.mark.parametrize("field", ["reason", "question", "possible_actions", "trigger_at"])
def test_follow_up_missing_a_required_item(field: str) -> None:
    """spec「缺少必填的项」：缺少字段。"""
    fields = _follow_up_fields()
    del fields[field]

    _assert_format_error(FollowUpRequest, fields)


def test_follow_up_question_with_only_whitespace() -> None:
    """spec「缺少必填的项」：问题是空白。"""
    _assert_format_error(FollowUpRequest, _follow_up_fields(question=" "))


def test_follow_up_trigger_time_without_a_time_zone() -> None:
    """spec「触发时间不带时区」。"""
    naive = datetime(2026, 11, 19, 14, 30)  # noqa: DTZ001

    _assert_format_error(FollowUpRequest, _follow_up_fields(trigger_at=naive))


# 给 PM 的 schema


@pytest.mark.parametrize(
    ("model", "decisions"),
    [
        (HoldingConclusion, ["hold", "add", "reduce", "close"]),
        (WatchConclusion, ["buy", "keep_watching", "demote", "remove"]),
        (CandidateConclusion, ["promote", "keep", "remove"]),
    ],
)
def test_schema_lists_only_the_allowed_decisions(
    model: type[BaseModel], decisions: list[str]
) -> None:
    """每种结论的 JSON schema 里，决定只列出这个状态允许的值（design D3）。"""
    decision = model.model_json_schema()["properties"]["decision"]

    assert decision["enum"] == decisions
