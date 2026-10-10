"""信号和无信号格式的测试。

场景见 openspec 的 signal-format spec「信号的格式」「评分的刻度」「无信号」。基准数据和 spec 一致。
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from tradingagentspro.contracts.signal import NoSignal, Signal, SubAgentOutput

OUTPUT: TypeAdapter[SubAgentOutput] = TypeAdapter(SubAgentOutput)
DATA_AS_OF = datetime(2026, 10, 14, 14, 30, tzinfo=UTC)
NO_SIGNAL_REASON = "最近一期财报还没有进状态库，无法判断"


def _signal(**changes: Any) -> dict[str, Any]:
    """基准信号的各项，changes 覆盖其中的项。"""
    fields = {
        "horizon": 5,
        "score": 1,
        "confidence": Decimal("0.6"),
        "data_as_of": DATA_AS_OF,
        "cited_entries": ("E-1", "E-2"),
        "rationale": "订单回暖，放量突破前高",
        "risks": "板块整体回调时跟跌",
    }
    return fields | changes


def _no_signal(**changes: Any) -> dict[str, Any]:
    """基准无信号的各项：不引用条目；changes 覆盖或补充其中的项。"""
    return {"reason": NO_SIGNAL_REASON} | changes


def _assert_format_error(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Signal.model_validate(fields)


def _assert_not_an_output(fields: dict[str, Any]) -> None:
    """既不是格式正确的信号，也不是格式正确的无信号。"""
    with pytest.raises(ValidationError):
        OUTPUT.validate_python(fields)


# 信号的格式


def test_complete_signal() -> None:
    """spec「完整的信号」。"""
    signal = Signal(
        horizon=5,
        score=1,
        confidence=Decimal("0.6"),
        data_as_of=DATA_AS_OF,
        cited_entries=("E-1", "E-2"),
        rationale="订单回暖，放量突破前高",
        risks="板块整体回调时跟跌",
    )
    citing_one = Signal.model_validate(_signal(cited_entries=("E-1",)))

    assert signal == Signal.model_validate(_signal())
    assert citing_one.cited_entries == ("E-1",)


@pytest.mark.parametrize(
    "field",
    ["horizon", "score", "confidence", "data_as_of", "cited_entries", "rationale", "risks"],
)
def test_missing_one_item(field: str) -> None:
    """spec「缺少一项」：错误指出缺少的是哪一项。"""
    fields = _signal()
    del fields[field]

    with pytest.raises(ValidationError) as raised:
        Signal.model_validate(fields)

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [("missing", (field,))]


@pytest.mark.parametrize("horizon", [0, -5, 2.5])
def test_horizon_is_not_a_positive_integer(horizon: float) -> None:
    """spec「评估窗口不是正整数」。"""
    _assert_format_error(_signal(horizon=horizon))


@pytest.mark.parametrize("confidence", ["0", "1"])
def test_confidence_bounds_inside(confidence: str) -> None:
    """spec「置信度的边界」：0 和 1 格式正确。"""
    signal = Signal.model_validate(_signal(confidence=Decimal(confidence)))

    assert signal.confidence == Decimal(confidence)


@pytest.mark.parametrize("confidence", ["-0.1", "1.1"])
def test_confidence_bounds_outside(confidence: str) -> None:
    """spec「置信度的边界」：-0.1 和 1.1 是格式错误。"""
    _assert_format_error(_signal(confidence=Decimal(confidence)))


@pytest.mark.parametrize(
    "cited_entries", [(), ("E-1", "  ")], ids=["不引用任何条目", "引用 E-1 和空白"]
)
def test_cited_entries(cited_entries: tuple[str, ...]) -> None:
    """spec「引用的条目」。"""
    _assert_format_error(_signal(cited_entries=cited_entries))


@pytest.mark.parametrize("changes", [{"rationale": "  "}, {"risks": ""}])
def test_text_with_only_whitespace(changes: dict[str, str]) -> None:
    """spec「只有空白的文字」。"""
    _assert_format_error(_signal(**changes))


def test_text_is_stripped() -> None:
    signal = Signal.model_validate(
        _signal(rationale=" 订单回暖，放量突破前高  ", cited_entries=(" E-1",))
    )

    assert (signal.rationale, signal.cited_entries) == ("订单回暖，放量突破前高", ("E-1",))


def test_data_as_of_without_a_time_zone() -> None:
    """spec「数据截至时刻不带时区」。"""
    naive = datetime(2026, 10, 14, 14, 30)  # noqa: DTZ001

    _assert_format_error(_signal(data_as_of=naive))


def test_extra_item() -> None:
    """spec「多出来的项」：另外写了股票 AAA。"""
    _assert_format_error(_signal(symbol="AAA"))


# 评分的刻度


@pytest.mark.parametrize("score", [-2, -1, 0, 1, 2])
def test_five_scores(score: int) -> None:
    """spec「五档评分」。"""
    assert Signal.model_validate(_signal(score=score)).score == score


@pytest.mark.parametrize("score", [-3, 3, 1.5])
def test_score_out_of_range_or_not_an_integer(score: float) -> None:
    """spec「评分超出范围或不是整数」。"""
    _assert_format_error(_signal(score=score))


# 无信号


def test_two_kinds_of_sub_agent_output() -> None:
    """spec「子 agent 的两种输出」。"""
    signal = OUTPUT.validate_python(_signal())
    no_signal = OUTPUT.validate_python(_no_signal())
    citing = OUTPUT.validate_python(_no_signal(cited_entries=("E-1",)))

    assert signal == Signal.model_validate(_signal())
    assert type(signal) is Signal
    assert no_signal == NoSignal(reason=NO_SIGNAL_REASON)
    assert no_signal.cited_entries == ()
    assert citing == NoSignal(reason=NO_SIGNAL_REASON, cited_entries=("E-1",))


@pytest.mark.parametrize(
    "fields",
    [_no_signal(reason="  "), {"cited_entries": ("E-1",)}],
    ids=["原因是空白", "只写了引用的条目"],
)
def test_no_signal_without_a_reason(fields: dict[str, Any]) -> None:
    """spec「无信号缺少原因」。"""
    _assert_not_an_output(fields)


@pytest.mark.parametrize(
    "changes", [{"score": 0}, {"horizon": 5}], ids=["写了评分 0", "写了评估窗口 5"]
)
def test_no_signal_has_no_signal_items(changes: dict[str, int]) -> None:
    """spec「无信号不写信号的项」。"""
    _assert_not_an_output(_no_signal(**changes))


def test_neither_signal_nor_no_signal() -> None:
    """spec「既不是信号也不是无信号」：没有任何项；缺少主要风险的基准信号。"""
    without_risks = _signal()
    del without_risks["risks"]

    _assert_not_an_output({})
    _assert_not_an_output(without_risks)


# 写出再读回（design D8）

_texts = st.text(min_size=1, max_size=20).filter(str.strip)
_offsets = st.integers(-(23 * 60 + 59), 23 * 60 + 59).map(
    lambda minutes: timezone(timedelta(minutes=minutes))
)
_signals = st.builds(
    Signal,
    horizon=st.integers(min_value=1, max_value=250),
    score=st.integers(min_value=-2, max_value=2),
    confidence=st.decimals(min_value=0, max_value=1, places=4),
    data_as_of=st.datetimes(
        min_value=datetime(2000, 1, 1),  # noqa: DTZ001
        max_value=datetime(2100, 1, 1),  # noqa: DTZ001
        timezones=_offsets,
    ),
    cited_entries=st.lists(_texts, min_size=1, max_size=3).map(tuple),
    rationale=_texts,
    risks=_texts,
)
_no_signals = st.builds(
    NoSignal, reason=_texts, cited_entries=st.lists(_texts, max_size=3).map(tuple)
)


@given(output=st.one_of(_signals, _no_signals))
def test_output_survives_a_json_round_trip(output: SubAgentOutput) -> None:
    """任意格式正确的信号和无信号，按 JSON 写出再读回，类型相同、对象相等（design D8）。"""
    restored = OUTPUT.validate_json(OUTPUT.dump_json(output))

    assert type(restored) is type(output)
    assert restored == output
