"""调度器配置的测试。场景见 openspec 的 case-types spec「研究要求的配置」和 case-queue spec
「队列配置」。
"""

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.scheduler import SchedulerConfig


def test_default_config() -> None:
    """case-types spec「默认配置」。"""
    assert SchedulerConfig().min_cited_agents == 2


@pytest.mark.parametrize("value", [1, 3])
def test_config_within_the_number_of_sub_agents(value: int) -> None:
    assert SchedulerConfig(min_cited_agents=value).min_cited_agents == value


@pytest.mark.parametrize("value", [0, 4])
def test_invalid_config_is_an_error(value: int) -> None:
    """case-types spec「配置不合法」：研究要求的子 agent 数是 0 或 4。"""
    with pytest.raises(ValidationError):
        SchedulerConfig(min_cited_agents=value)


def test_default_queue_config() -> None:
    """case-queue spec「默认配置」：并发上限是 3。"""
    assert SchedulerConfig().max_concurrent_cases == 3


def test_concurrency_limit_of_one_is_valid() -> None:
    assert SchedulerConfig(max_concurrent_cases=1).max_concurrent_cases == 1


def test_invalid_queue_config_is_an_error() -> None:
    """case-queue spec「配置不合法」：并发上限是 0，报错的是下限。"""
    with pytest.raises(ValidationError) as error:
        SchedulerConfig(max_concurrent_cases=0)

    assert [e["type"] for e in error.value.errors()] == ["greater_than_equal"]
