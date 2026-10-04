"""执行配置的测试。场景见 openspec 的 order-lifecycle spec「执行配置」。"""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.execution import ExecutionConfig


def test_default_config() -> None:
    assert ExecutionConfig().unknown_lookup_delay == timedelta(seconds=30)


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(seconds=-1)])
def test_lookup_delay_must_be_positive(delay: timedelta) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(unknown_lookup_delay=delay)
