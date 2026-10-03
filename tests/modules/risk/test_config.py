"""风控阈值配置的测试。场景见 openspec 的 risk-rules spec「风控阈值配置」。"""

from decimal import Decimal

from tradingagentspro.modules.risk import RiskConfig


def test_defaults_are_used_when_no_config_is_given() -> None:
    config = RiskConfig()

    assert config.max_trade_risk_ratio == Decimal("0.01")
    assert config.max_total_risk_ratio == Decimal("0.05")
    assert config.max_position_ratio == Decimal("0.20")
    assert config.max_sector_ratio == Decimal("0.40")
    assert config.max_positions == 8
    assert config.cost_buffer_ratio == Decimal("0.005")
    assert config.min_trade_interval_sessions == 1
    assert config.emergency_threshold_min == Decimal("0.03")
    assert config.emergency_threshold_max == Decimal("0.20")
    assert config.emergency_threshold_default == Decimal("0.08")
    assert config.price_deviation_min == Decimal("0.01")
    assert config.price_deviation_max == Decimal("0.10")
    assert config.price_deviation_default == Decimal("0.03")
