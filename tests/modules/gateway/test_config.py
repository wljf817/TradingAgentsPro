"""Agent 网关配置的测试。场景见 openspec 的 signal-format spec「信号的配置」。"""

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.gateway import GatewayConfig


def test_default_config() -> None:
    """spec「默认配置」：评估窗口的选项是 3、5、20、60。"""
    assert GatewayConfig().signal_horizons == (3, 5, 20, 60)


def test_one_horizon_is_valid() -> None:
    assert GatewayConfig(signal_horizons=(5,)).signal_horizons == (5,)


@pytest.mark.parametrize("horizons", [(), (0, 5)], ids=["空", "0 和 5"])
def test_invalid_config_is_an_error(horizons: tuple[int, ...]) -> None:
    """spec「配置不合法」：评估窗口的选项为空，或者是 0 和 5。"""
    with pytest.raises(ValidationError):
        GatewayConfig(signal_horizons=horizons)
