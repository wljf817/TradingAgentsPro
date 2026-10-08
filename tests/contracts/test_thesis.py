"""关注论点格式的测试。场景见 openspec 的 stock-pool spec「关注论点的格式」。"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.contracts.thesis import (
    Catalyst,
    CatalystReference,
    Comparison,
    Condition,
    ConditionKind,
    ReferenceKind,
    WatchThesis,
)

PRICE, CHANGE, VOLUME = ConditionKind.PRICE, ConditionKind.CHANGE, ConditionKind.VOLUME
AT_LEAST, AT_MOST = Comparison.AT_LEAST, Comparison.AT_MOST


def _condition(kind: ConditionKind, comparison: Comparison, value: int | str) -> dict[str, Any]:
    return {"kind": kind, "comparison": comparison, "value": Decimal(value)}


def _catalyst(**changes: Any) -> dict[str, Any]:
    """基准关注论点的催化剂：Q3 财报，截止日期 2026-10-29，不引用；changes 覆盖其中的字段。"""
    return {"description": "Q3 财报", "deadline": date(2026, 10, 29), "reference": None} | changes


def _thesis(**changes: Any) -> dict[str, Any]:
    """spec 的基准关注论点的字段，changes 覆盖其中的字段。"""
    fields = {
        "rationale": "新品周期带动收入加速",
        "entry_conditions": (_condition(PRICE, AT_MOST, 50),),
        "catalyst": _catalyst(),
        "invalidation": "毛利率连续两个季度下滑",
        "review_day": date(2026, 10, 21),
    }
    return fields | changes


def _assert_format_error(model: type[WatchThesis | Condition], fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(fields)


def test_complete_watch_thesis() -> None:
    """spec「完整的关注论点」。"""
    thesis = WatchThesis(
        rationale="新品周期带动收入加速",
        entry_conditions=(Condition(kind=PRICE, comparison=AT_MOST, value=Decimal(50)),),
        catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
        invalidation="毛利率连续两个季度下滑",
        review_day=date(2026, 10, 21),
    )

    assert thesis == WatchThesis.model_validate(_thesis())
    assert thesis.catalyst.reference is None


@pytest.mark.parametrize(
    "field", ["rationale", "entry_conditions", "catalyst", "invalidation", "review_day"]
)
def test_missing_one_item(field: str) -> None:
    """spec「缺少一项」：缺少字段。"""
    fields = _thesis()
    del fields[field]

    _assert_format_error(WatchThesis, fields)


def test_missing_entry_conditions_means_not_even_one() -> None:
    """spec「缺少一项」：入场条件一条都没有。"""
    _assert_format_error(WatchThesis, _thesis(entry_conditions=()))


@pytest.mark.parametrize(
    "changes",
    [
        {"rationale": "  "},
        {"invalidation": "  "},
        {"catalyst": _catalyst(description="  ")},
    ],
)
def test_text_with_only_whitespace(changes: dict[str, Any]) -> None:
    """spec「只有空白的文字」：看好理由、证伪条件或催化剂描述。"""
    _assert_format_error(WatchThesis, _thesis(**changes))


def test_text_is_stripped() -> None:
    thesis = WatchThesis.model_validate(_thesis(rationale="  新品周期带动收入加速 "))

    assert thesis.rationale == "新品周期带动收入加速"


@pytest.mark.parametrize(
    "fields",
    [
        _condition(PRICE, AT_MOST, 50),
        _condition(PRICE, AT_LEAST, 60),
        _condition(CHANGE, AT_MOST, "-0.05"),
        _condition(CHANGE, AT_LEAST, "0.03"),
        _condition(VOLUME, AT_LEAST, 2_000_000),
    ],
)
def test_three_kinds_of_conditions(fields: dict[str, Any]) -> None:
    """spec「三类条件」：正确的条件。"""
    condition = Condition.model_validate(fields)

    assert (condition.kind, condition.comparison, condition.value) == (
        fields["kind"],
        fields["comparison"],
        fields["value"],
    )


@pytest.mark.parametrize(
    "fields",
    [
        _condition(PRICE, AT_MOST, 0),
        _condition(VOLUME, AT_LEAST, 0),
        _condition(VOLUME, AT_MOST, 2_000_000),
    ],
)
def test_invalid_conditions(fields: dict[str, Any]) -> None:
    """spec「三类条件」：格式错误的条件。"""
    _assert_format_error(Condition, fields)


@pytest.mark.parametrize(
    "reference",
    [
        CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="E-1"),
        CatalystReference(kind=ReferenceKind.EVENT, ref_id="V-7"),
        None,
    ],
)
def test_catalyst_references_a_schedule_item_or_an_event(
    reference: CatalystReference | None,
) -> None:
    """spec「催化剂引用日程项或事件」：引用日程项、引用事件或者不引用。"""
    thesis = WatchThesis.model_validate(_thesis(catalyst=_catalyst(reference=reference)))

    assert thesis.catalyst.reference == reference


def test_catalyst_reference_without_an_id() -> None:
    """spec「催化剂引用日程项或事件」：引用事件，ID 为空。"""
    reference = {"kind": ReferenceKind.EVENT, "ref_id": ""}

    _assert_format_error(WatchThesis, _thesis(catalyst=_catalyst(reference=reference)))


def test_fields_outside_the_thesis_are_rejected() -> None:
    _assert_format_error(WatchThesis, _thesis(note="补充说明"))
