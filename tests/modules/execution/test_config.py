"""执行配置的测试。场景见 openspec 的 order-lifecycle spec「执行配置」和 close-reconciliation spec
「收盘对账配置」。
"""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.execution import ExecutionConfig


def test_default_config() -> None:
    """order-lifecycle spec「默认配置」和 close-reconciliation spec「默认配置」。"""
    config = ExecutionConfig()

    assert config.unknown_lookup_delay == timedelta(seconds=30)
    assert config.stop_immediate_retries == 1
    assert config.stagger_buys_and_stops is False
    assert config.close_reconcile_delay == timedelta(minutes=10)


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(seconds=-1)])
def test_lookup_delay_must_be_positive(delay: timedelta) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(unknown_lookup_delay=delay)


def test_immediate_retries_must_not_be_negative() -> None:
    """order-lifecycle spec「立即重试次数小于 0」。"""
    with pytest.raises(ValidationError):
        ExecutionConfig(stop_immediate_retries=-1)


def test_stagger_buys_and_stops_can_be_turned_on() -> None:
    """order-lifecycle spec「打开买单和止损单错开」。"""
    config = ExecutionConfig(stagger_buys_and_stops=True)

    assert config.stagger_buys_and_stops is True


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(minutes=-1)])
def test_close_reconcile_delay_must_be_positive(delay: timedelta) -> None:
    """close-reconciliation spec「延迟不大于 0」。"""
    with pytest.raises(ValidationError):
        ExecutionConfig(close_reconcile_delay=delay)
