"""紧急退出配置的测试。场景见 openspec 的 emergency-exit spec「紧急退出配置」。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.emergency import EmergencyConfig


def test_default_config() -> None:
    """spec「默认配置」。"""
    assert EmergencyConfig().daily_loss_breaker_ratio == Decimal("0.07")


@pytest.mark.parametrize("ratio", [Decimal(0), Decimal(1)])
def test_breaker_ratio_out_of_range_is_an_error(ratio: Decimal) -> None:
    """spec「熔断比例超出范围」：熔断比例为 0 和 1 时报错。"""
    with pytest.raises(ValidationError):
        EmergencyConfig(daily_loss_breaker_ratio=ratio)
