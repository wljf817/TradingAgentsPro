"""Agent 网关配置的测试。场景见 openspec 的 signal-format spec「信号的配置」和 task-dispatch spec
「派发的配置」。
"""

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.gateway import GatewayConfig


def test_default_config() -> None:
    """signal-format spec「默认配置」：评估窗口的选项是 3、5、20、60。"""
    assert GatewayConfig().signal_horizons == (3, 5, 20, 60)


def test_one_horizon_is_valid() -> None:
    assert GatewayConfig(signal_horizons=(5,)).signal_horizons == (5,)


@pytest.mark.parametrize("horizons", [(), (0, 5)], ids=["空", "0 和 5"])
def test_invalid_config_is_an_error(horizons: tuple[int, ...]) -> None:
    """signal-format spec「配置不合法」：评估窗口的选项为空，或者是 0 和 5。"""
    with pytest.raises(ValidationError):
        GatewayConfig(signal_horizons=horizons)


def test_default_dispatch_config() -> None:
    """task-dispatch spec「默认配置」：PM 的任务数上限 6，PM 的轮次上限 2，Critic 的补充研究
    额度 2，任务时限 5 分钟。
    """
    config = GatewayConfig()

    assert config.max_pm_tasks == 6
    assert config.max_pm_rounds == 2
    assert config.max_critic_tasks == 2
    assert config.task_timeout == timedelta(minutes=5)


def test_smallest_valid_dispatch_config() -> None:
    """四项都取最小的合法值：1 个、1 轮、1 个、1 秒。"""
    config = GatewayConfig(
        max_pm_tasks=1, max_pm_rounds=1, max_critic_tasks=1, task_timeout=timedelta(seconds=1)
    )

    assert (config.max_pm_tasks, config.max_pm_rounds, config.max_critic_tasks) == (1, 1, 1)
    assert config.task_timeout == timedelta(seconds=1)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_pm_tasks": 0},
        {"max_pm_rounds": 0},
        {"max_critic_tasks": 0},
        {"task_timeout": timedelta(0)},
    ],
    ids=["PM 的任务数上限 0", "PM 的轮次上限 0", "Critic 的补充研究额度 0", "任务时限 0 秒"],
)
def test_invalid_dispatch_config_is_an_error(changes: dict[str, Any]) -> None:
    """task-dispatch spec「配置不合法」：都配置报错，错误指出这一项必须大于 0。"""
    with pytest.raises(ValidationError) as raised:
        GatewayConfig.model_validate(changes)

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [
        ("greater_than", tuple(changes))
    ]
