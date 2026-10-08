"""股票池配置的测试。场景见 openspec 的 stock-pool spec「股票池配置」。"""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.contracts.listing import Exchange, SecurityType
from tradingagentspro.modules.stockpool import StockPoolConfig


def test_default_config() -> None:
    """spec「默认配置」。"""
    config = StockPoolConfig()

    assert config.max_pool_size == 30
    assert config.max_watch == 8
    assert config.max_daily_changes == 10
    assert config.candidate_ttl_sessions == 10
    assert config.readd_cooldown_sessions == 5
    assert config.min_average_dollar_volume == Decimal(10_000_000)
    assert config.allowed_exchanges == {Exchange.NYSE, Exchange.NASDAQ}
    assert config.allowed_security_types == {SecurityType.COMMON_STOCK, SecurityType.ADR}


@pytest.mark.parametrize(
    "changes",
    [
        {"max_pool_size": 0},
        {"max_watch": 0},
        {"max_daily_changes": 0},
        {"candidate_ttl_sessions": 0},
        {"readd_cooldown_sessions": 0},
        {"min_average_dollar_volume": Decimal(0)},
        {"allowed_exchanges": frozenset()},
        {"allowed_security_types": frozenset()},
    ],
)
def test_invalid_config_is_an_error(changes: dict[str, Any]) -> None:
    """spec「配置不合法」：数值项为 0，或者允许的范围为空。"""
    with pytest.raises(ValidationError):
        StockPoolConfig(**changes)
