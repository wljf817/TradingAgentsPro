"""调度器配置的测试。场景见 openspec 的 case-types spec「研究要求的配置」。"""

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.scheduler import SchedulerConfig


def test_default_config() -> None:
    """spec「默认配置」。"""
    assert SchedulerConfig().min_cited_agents == 2


@pytest.mark.parametrize("value", [1, 3])
def test_config_within_the_number_of_sub_agents(value: int) -> None:
    assert SchedulerConfig(min_cited_agents=value).min_cited_agents == value


@pytest.mark.parametrize("value", [0, 4])
def test_invalid_config_is_an_error(value: int) -> None:
    """spec「配置不合法」：研究要求的子 agent 数是 0 或 4。"""
    with pytest.raises(ValidationError):
        SchedulerConfig(min_cited_agents=value)
