"""行情配置的测试。场景见 openspec 的 market-data spec「行情配置」。"""

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.marketdata import MarketDataConfig


def test_default_config() -> None:
    """spec「默认配置」。"""
    assert MarketDataConfig().reference_symbol == "SPY"


def test_empty_reference_symbol_is_an_error() -> None:
    """spec「参考股票为空」。"""
    with pytest.raises(ValidationError):
        MarketDataConfig(reference_symbol="")
