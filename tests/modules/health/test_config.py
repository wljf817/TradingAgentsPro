"""健康配置的测试。场景见 openspec 的 degraded-mode spec「健康配置」。"""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.health import HealthConfig


def test_default_config() -> None:
    """spec「默认配置」。"""
    config = HealthConfig()

    assert config.max_quote_delay == timedelta(seconds=60)
    assert config.scheduler_heartbeat_timeout == timedelta(seconds=300)


@pytest.mark.parametrize("field", ["max_quote_delay", "scheduler_heartbeat_timeout"])
@pytest.mark.parametrize("value", [timedelta(0), timedelta(seconds=-1)])
def test_config_not_greater_than_zero_is_an_error(field: str, value: timedelta) -> None:
    """spec「配置不大于 0」。"""
    with pytest.raises(ValidationError):
        HealthConfig(**{field: value})
