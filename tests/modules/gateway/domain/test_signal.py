"""收到输出时的校验的测试：子 agent 的输出和 PM 结论里的信号。

场景见 openspec 的 signal-format spec「收到输出时的校验」「结论里的信号」，以及「信号的配置」的
「改成别的选项」。基准数据见 tests/modules/gateway/builders.py：当前时刻 2026-10-14 15:00Z。
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from tests.modules.gateway.builders import (
    NO_SIGNAL,
    NOW,
    SIGNAL,
    at,
    candidate,
    ddd,
    eee,
    exploration,
    holding,
    signal,
    watch,
)
from tradingagentspro.contracts.conclusion import Conclusion
from tradingagentspro.contracts.signal import Signal
from tradingagentspro.modules.gateway import (
    GatewayConfig,
    SignalRejection,
    conclusion_signal_rejection,
    signal_rejection,
)

CONFIG = GatewayConfig()
HORIZON_NOT_OFFERED = SignalRejection.HORIZON_NOT_OFFERED
DATA_AFTER_NOW = SignalRejection.DATA_AFTER_NOW
UTC_PLUS_8 = timezone(timedelta(hours=8))
UTC_MINUS_5 = timezone(timedelta(hours=-5))


# 收到输出时的校验


@pytest.mark.parametrize("horizon", [3, 5, 20, 60])
def test_horizons_in_the_options(horizon: int) -> None:
    """spec「选项里的评估窗口」。"""
    assert signal_rejection(signal(horizon=horizon), NOW, CONFIG) is None


@pytest.mark.parametrize("horizon", [1, 4, 61])
def test_horizons_outside_the_options(horizon: int) -> None:
    """spec「选项以外的评估窗口」。"""
    assert signal_rejection(signal(horizon=horizon), NOW, CONFIG) is HORIZON_NOT_OFFERED


@pytest.mark.parametrize(
    "data_as_of",
    [at(15), datetime(2026, 10, 14, 23, tzinfo=UTC_PLUS_8), at(20, day=1)],
    ids=["15:00Z", "23:00+08:00", "10-01 20:00Z"],
)
def test_data_as_of_not_after_now(data_as_of: datetime) -> None:
    """spec「数据截至时刻的边界」：不晚于现在都合法，和时区无关。"""
    assert signal_rejection(signal(data_as_of=data_as_of), NOW, CONFIG) is None


@pytest.mark.parametrize(
    "data_as_of",
    [at(15, 0, 1), datetime(2026, 10, 14, 11, tzinfo=UTC_MINUS_5)],
    ids=["15:00:01Z", "11:00-05:00"],
)
def test_data_as_of_after_now(data_as_of: datetime) -> None:
    """spec「数据截至时刻的边界」：晚于现在不合法。"""
    assert signal_rejection(signal(data_as_of=data_as_of), NOW, CONFIG) is DATA_AFTER_NOW


def test_first_failing_check_is_given() -> None:
    """spec「两项都不通过」：评估窗口 4、数据截至 16:00Z，给出评估窗口。"""
    both = signal(horizon=4, data_as_of=at(16))

    assert signal_rejection(both, NOW, CONFIG) is HORIZON_NOT_OFFERED


def test_no_signal_is_valid_once_well_formed() -> None:
    """spec「无信号格式正确就合法」。"""
    assert signal_rejection(NO_SIGNAL, NOW, CONFIG) is None


def test_changing_the_config_does_not_affect_written_signals() -> None:
    """spec「改配置不影响已经写入的信号」：15:00Z 收到并写入评估窗口 3 的信号；选项改为 5 和 20
    以后，用写入的各项重新构造，格式正确、和写入的相同。新收到的评估窗口 3 的信号才不合法。
    """
    written = signal(horizon=3)
    assert signal_rejection(written, NOW, CONFIG) is None
    changed = GatewayConfig(signal_horizons=(5, 20))

    restored = Signal.model_validate_json(written.model_dump_json())

    assert restored == written
    assert restored.horizon == 3
    assert signal_rejection(signal(horizon=3), NOW, changed) is HORIZON_NOT_OFFERED


# 结论里的信号


@pytest.mark.parametrize(
    "conclude", [holding, watch, candidate], ids=["持仓持有", "关注继续关注", "候选保持"]
)
def test_every_kind_of_conclusion_is_checked(conclude: Callable[[Signal], Conclusion]) -> None:
    """spec「每种结论的信号都检查」：基准信号合法，评估窗口 10 的信号不合法。"""
    valid = conclude(SIGNAL)
    invalid = conclude(signal(horizon=10))

    assert conclusion_signal_rejection(valid, NOW, CONFIG) is None
    assert conclusion_signal_rejection(invalid, NOW, CONFIG) is HORIZON_NOT_OFFERED


def test_pm_data_as_of_after_now() -> None:
    """spec「PM 的数据截至时刻晚于现在」：候选的结论（保持），数据截至 15:00:01Z。"""
    conclusion = candidate(signal(data_as_of=at(15, 0, 1)))

    assert conclusion_signal_rejection(conclusion, NOW, CONFIG) is DATA_AFTER_NOW


@pytest.mark.parametrize(
    ("conclusion", "rejection"),
    [
        (exploration(ddd(), eee(signal(horizon=10))), HORIZON_NOT_OFFERED),
        (exploration(ddd(signal(data_as_of=at(16))), eee(signal(horizon=10))), DATA_AFTER_NOW),
        (exploration(ddd(), eee()), None),
        (exploration(), None),
    ],
    ids=["EEE 的评估窗口 10", "DDD 在前", "都是基准信号", "不加入候选"],
)
def test_every_addition_is_checked(
    conclusion: Conclusion, rejection: SignalRejection | None
) -> None:
    """spec「寻股的每只候选都检查」：按加入的顺序给出第一个不通过的信号的原因。"""
    assert conclusion_signal_rejection(conclusion, NOW, CONFIG) is rejection


# 信号的配置


def test_other_horizon_options() -> None:
    """spec「改成别的选项」：选项是 5 和 20。"""
    config = GatewayConfig(signal_horizons=(5, 20))

    assert signal_rejection(signal(horizon=3), NOW, config) is HORIZON_NOT_OFFERED
    assert signal_rejection(signal(horizon=20), NOW, config) is None
