"""跟进审批配置的测试。场景见 openspec 的 follow-ups spec「跟进的配置」；「改配置改变审批」在
test_service.py。
"""

from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.followups import FollowUpConfig


def test_default_config() -> None:
    """spec「默认配置」。"""
    config = FollowUpConfig()

    assert config.max_pending_per_stock == 2
    assert config.max_trigger_sessions == 60
    assert config.max_daily_requests == 5


@pytest.mark.parametrize(
    "changes",
    [
        {"max_pending_per_stock": 0},
        {"max_trigger_sessions": 0},
        {"max_daily_requests": 0},
    ],
    ids=["每只股票的待执行跟进上限 0", "触发时间最远间隔 0", "每天的申请次数上限 0"],
)
def test_invalid_config_is_an_error(changes: dict[str, Any]) -> None:
    """spec「配置不合法」。"""
    with pytest.raises(ValidationError):
        FollowUpConfig(**changes)


def test_one_is_valid() -> None:
    """三项都取 1。"""
    config = FollowUpConfig(max_pending_per_stock=1, max_trigger_sessions=1, max_daily_requests=1)

    assert (config.max_pending_per_stock, config.max_trigger_sessions) == (1, 1)
    assert config.max_daily_requests == 1
