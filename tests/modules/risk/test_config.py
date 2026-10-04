"""风控阈值配置的测试。场景见 openspec 的 risk-rules spec「风控阈值配置」。"""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.risk import RiskConfig


def test_defaults_are_used_when_no_config_is_given() -> None:
    config = RiskConfig()

    assert config.max_trade_risk_ratio == Decimal("0.01")
    assert config.max_total_risk_ratio == Decimal("0.05")
    assert config.max_position_ratio == Decimal("0.20")
    assert config.max_sector_ratio == Decimal("0.40")
    assert config.max_positions == 8
    assert config.cost_buffer_ratio == Decimal("0.005")
    assert config.commission_per_order == Decimal(2)
    assert config.max_round_trip_commission_ratio == Decimal("0.01")
    assert config.min_trade_interval_sessions == 1
    assert config.emergency_threshold_min == Decimal("0.03")
    assert config.emergency_threshold_max == Decimal("0.20")
    assert config.emergency_threshold_default == Decimal("0.08")
    assert config.price_deviation_min == Decimal("0.01")
    assert config.price_deviation_max == Decimal("0.10")
    assert config.price_deviation_default == Decimal("0.03")


@pytest.mark.parametrize(
    "changes",
    [{"commission_per_order": Decimal(-1)}, {"max_round_trip_commission_ratio": Decimal(0)}],
)
def test_commission_config_out_of_range_is_an_error(changes: dict[str, Any]) -> None:
    """spec「佣金配置的取值范围」：每笔佣金为负数、往返佣金占比上限为 0 时报错。"""
    with pytest.raises(ValidationError):
        RiskConfig(**changes)


def test_zero_commission_is_allowed() -> None:
    """spec「佣金配置的取值范围」：每笔佣金可以为 0。"""
    assert RiskConfig(commission_per_order=Decimal(0)).commission_per_order == 0
