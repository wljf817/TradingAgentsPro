"""通知模块配置的测试。场景见 openspec 的 discord-notification spec「确认请求和截止时刻」。"""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tradingagentspro.modules.notification import NotificationConfig


def test_default_confirmation_timeout_is_30_minutes() -> None:
    """spec「确认请求和截止时刻」：确认超时默认 30 分钟。"""
    assert NotificationConfig().confirmation_timeout == timedelta(minutes=30)


@pytest.mark.parametrize("minutes", [0, -1])
def test_confirmation_timeout_must_be_positive(minutes: int) -> None:
    """spec「确认超时不是正数」。"""
    with pytest.raises(ValidationError):
        NotificationConfig(confirmation_timeout=timedelta(minutes=minutes))
