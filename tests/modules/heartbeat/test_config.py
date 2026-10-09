"""定时心跳配置的测试。场景见 openspec 的 review-dates spec「复查日与催化剂的配置」和 close-check
spec「收盘检查的配置」。
"""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.heartbeat import HeartbeatConfig


def test_default_config() -> None:
    """review-dates spec 和 close-check spec 的「默认配置」。"""
    config = HeartbeatConfig()

    assert config.min_review_sessions == 3
    assert config.max_review_sessions == 20
    assert config.default_review_sessions == 5
    assert config.max_postpone_loss == Decimal("0.05")
    assert config.max_schedule_lag_sessions == 2
    assert config.max_deadline_sessions == 60
    assert config.max_daily_evaluations == 3
    assert config.min_viable_candidates == 5
    assert config.min_strong_candidates == 2
    assert config.expiring_sessions == 3
    assert config.exploration_cooldown_sessions == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"min_review_sessions": 0},
        {"max_schedule_lag_sessions": 0},
        {"max_deadline_sessions": 0},
        {"min_review_sessions": 6},
        {"max_review_sessions": 4},
        {"max_postpone_loss": Decimal(0)},
        {"max_postpone_loss": Decimal(1)},
        {"max_daily_evaluations": 0},
        {"min_viable_candidates": 0},
        {"min_strong_candidates": 0},
        {"expiring_sessions": 0},
        {"exploration_cooldown_sessions": 0},
    ],
    ids=[
        "最短间隔 0",
        "日程项后的截止期限 0",
        "截止日期最长间隔 0",
        "最短间隔 6 大于默认间隔",
        "最长间隔 4 小于默认间隔",
        "亏损幅度 0",
        "亏损幅度 1",
        "候选评估上限 0",
        "有效候选的下限 0",
        "好候选的下限 0",
        "临近过期的期限 0",
        "寻股的冷却期 0",
    ],
)
def test_invalid_config_is_an_error(changes: dict[str, Any]) -> None:
    """review-dates spec 和 close-check spec 的「配置不合法」。"""
    with pytest.raises(ValidationError):
        HeartbeatConfig(**changes)


def test_one_session_is_valid() -> None:
    """交易日个数都取 1：最短 = 默认 = 最长 = 1。"""
    config = HeartbeatConfig(
        min_review_sessions=1,
        default_review_sessions=1,
        max_review_sessions=1,
        max_schedule_lag_sessions=1,
        max_deadline_sessions=1,
    )

    assert (config.min_review_sessions, config.max_review_sessions) == (1, 1)
    assert (config.max_schedule_lag_sessions, config.max_deadline_sessions) == (1, 1)


def test_equal_review_intervals_are_valid() -> None:
    """最短 = 默认 = 最长 = 5。"""
    config = HeartbeatConfig(min_review_sessions=5, max_review_sessions=5)

    assert (config.min_review_sessions, config.default_review_sessions) == (5, 5)
    assert config.max_review_sessions == 5


def test_close_check_items_of_one_are_valid() -> None:
    """收盘检查的五项都取 1。"""
    config = HeartbeatConfig(
        max_daily_evaluations=1,
        min_viable_candidates=1,
        min_strong_candidates=1,
        expiring_sessions=1,
        exploration_cooldown_sessions=1,
    )

    assert (config.max_daily_evaluations, config.min_viable_candidates) == (1, 1)
    assert (config.min_strong_candidates, config.expiring_sessions) == (1, 1)
    assert config.exploration_cooldown_sessions == 1
