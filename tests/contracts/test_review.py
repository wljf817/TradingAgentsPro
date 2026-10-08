"""Critic 审核和 PM 回应格式的测试。

场景见 openspec 的 case-stages spec「Critic 审核和 PM 回应的格式」，每个场景一个测试。
"""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from tradingagentspro.contracts.conclusion import PlanDraft
from tradingagentspro.contracts.review import (
    CriticReview,
    PlanResponse,
    ResponseDecision,
    Verdict,
)
from tradingagentspro.contracts.trade_plan import OrderType

APPROVE, CHALLENGE, VETO = Verdict.APPROVE, Verdict.CHALLENGE, Verdict.VETO
MAINTAIN, MODIFY, WITHDRAW = (
    ResponseDecision.MAINTAIN,
    ResponseDecision.MODIFY,
    ResponseDecision.WITHDRAW,
)
CHALLENGES = ("止损价 45 离限价只有 10%，财报前波动更大", "引用的 R-2 是三天前的数据")
REPLIES = ("止损价改为 46，股数减为 60", "R-5 是今天的数据，结论不变")


def _review(verdict: Verdict, **changes: Any) -> dict[str, Any]:
    """Critic 的一次审核：理由"止损太近"，changes 覆盖或补充其中的字段。"""
    return {"verdict": verdict, "rationale": "止损太近"} | changes


def _plan(**changes: Any) -> dict[str, Any]:
    """修改后的方案条款：60 股，限价单，限价 50，止损价 46，紧急阈值 0.08；changes 覆盖其中的字段，
    值为 None 表示不写。
    """
    fields = {
        "quantity": 60,
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal(50),
        "stop_price": Decimal(46),
        "emergency_threshold": Decimal("0.08"),
    }
    return fields | changes


def _response(decision: ResponseDecision, **changes: Any) -> dict[str, Any]:
    """PM 的一次回应：两条回应，changes 覆盖或补充其中的字段。"""
    return {"decision": decision, "replies": REPLIES} | changes


def _assert_format_error(model: type[BaseModel], fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(fields)


# Critic 的审核


def test_valid_reviews() -> None:
    """spec「合法的审核」。"""
    challenge = CriticReview.model_validate(
        _review(CHALLENGE, challenges=CHALLENGES, cited_results=("R-5",))
    )
    approval = CriticReview.model_validate(_review(APPROVE, rationale="风险收益比合理"))

    assert challenge.verdict is CHALLENGE
    assert challenge.challenges == CHALLENGES
    assert challenge.cited_results == ("R-5",)
    assert (approval.verdict, approval.challenges, approval.cited_results) == (APPROVE, (), ())


@pytest.mark.parametrize(
    "fields",
    [
        _review(CHALLENGE),
        _review(APPROVE, challenges=CHALLENGES[:1]),
        _review(VETO, challenges=CHALLENGES[:1]),
    ],
    ids=["质询不写质询", "通过写了质询", "否决写了质询"],
)
def test_challenges_must_match_the_verdict(fields: dict[str, Any]) -> None:
    """spec「质询和结论不一致」。"""
    _assert_format_error(CriticReview, fields)


@pytest.mark.parametrize(
    "fields",
    [
        _review(VETO, rationale=""),
        _review(VETO, rationale="  "),
        _review(CHALLENGE, challenges=(CHALLENGES[0], "")),
        _review(CHALLENGE, challenges=(CHALLENGES[0], " ")),
    ],
    ids=["理由为空", "理由只有空白", "一条质询为空", "一条质询只有空白"],
)
def test_empty_rationale_or_challenge(fields: dict[str, Any]) -> None:
    """spec「审核的理由或质询为空」。"""
    _assert_format_error(CriticReview, fields)


def test_review_fields_outside_the_spec_are_rejected() -> None:
    _assert_format_error(CriticReview, _review(APPROVE, supplementary_tasks=("T-1",)))


# PM 的回应


def test_valid_responses() -> None:
    """spec「合法的回应」。"""
    modified = PlanResponse.model_validate(_response(MODIFY, plan=_plan()))
    withdrawn = PlanResponse.model_validate(_response(WITHDRAW, replies=("同意，撤回方案",)))

    assert modified.plan == PlanDraft.model_validate(_plan())
    assert modified.replies == REPLIES
    assert (withdrawn.decision, withdrawn.plan) == (WITHDRAW, None)


@pytest.mark.parametrize(
    "fields",
    [_response(MODIFY), _response(MAINTAIN, plan=_plan())],
    ids=["修改不写方案条款", "维持写了方案条款"],
)
def test_modification_must_match_the_plan(fields: dict[str, Any]) -> None:
    """spec「修改和方案条款不一致」。"""
    _assert_format_error(PlanResponse, fields)


@pytest.mark.parametrize(
    "plan",
    [
        _plan(order_type=OrderType.MARKET, limit_price=None),
        _plan(stop_price=None),
        _plan(emergency_threshold=None),
        _plan(quantity=None),
        _plan(limit_price=Decimal("50.005")),
    ],
    ids=["市价单", "限价单但没写止损价", "没写紧急阈值", "没写股数", "限价 50.005"],
)
def test_modified_plan_is_checked_as_an_opening_plan(plan: dict[str, Any]) -> None:
    """spec「修改后的方案条款按开仓方案检查」。"""
    _assert_format_error(PlanResponse, _response(MODIFY, plan=plan))


@pytest.mark.parametrize(
    "replies", [(), (REPLIES[0], ""), (REPLIES[0], "  ")], ids=["回应为空", "一条为空", "一条空白"]
)
def test_response_needs_replies(replies: tuple[str, ...]) -> None:
    """spec「没有回应」。"""
    _assert_format_error(PlanResponse, _response(MAINTAIN, replies=replies))
