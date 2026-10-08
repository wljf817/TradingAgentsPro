"""调度器配置的测试。场景见 openspec 的 case-types spec「研究要求的配置」、case-queue spec
「队列配置」和 case-stages spec「案件阶段的配置」。
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


def test_default_case_stage_config() -> None:
    """case-stages spec「默认配置」：结论最多重交 2 次，质询最多 2 轮。"""
    config = SchedulerConfig()

    assert (config.max_resubmissions, config.max_challenge_rounds) == (2, 2)


def test_case_stage_config_lower_bounds_are_valid() -> None:
    """不重交（0）和质询最多一轮（1）都合法。"""
    config = SchedulerConfig(max_resubmissions=0, max_challenge_rounds=1)

    assert (config.max_resubmissions, config.max_challenge_rounds) == (0, 1)


@pytest.mark.parametrize(
    "changes", [{"max_resubmissions": -1}, {"max_challenge_rounds": 0}], ids=["重交 -1", "质询 0"]
)
def test_invalid_case_stage_config_is_an_error(changes: dict[str, int]) -> None:
    """case-stages spec「配置不合法」：报错的是下限。"""
    with pytest.raises(ValidationError) as error:
        SchedulerConfig.model_validate(changes)

    assert [e["type"] for e in error.value.errors()] == ["greater_than_equal"]
