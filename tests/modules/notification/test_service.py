"""通知用例的测试：截止时刻、被风控拒绝的方案，以及发不出去时的处理。

每个测试对应 openspec 的 discord-notification spec 里的一个场景。
"""

import logging
from datetime import datetime, timedelta

import pytest

from tests.fakes.clock import FakeClock
from tests.fakes.notifier import FakeNotifier
from tests.modules.notification.builders import (
    DEADLINE,
    REQUESTED_AT,
    p1,
    p1_decision,
)
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    ConfirmationRequest,
    NotificationConfig,
    NotificationService,
    NotificationUnavailableError,
    Notifier,
    PendingConfirmation,
)


def _service(
    config: NotificationConfig | None = None,
) -> tuple[NotificationService, FakeNotifier]:
    notifier = FakeNotifier()
    checked: Notifier = notifier  # 让 mypy 检查替身满足端口
    clock = FakeClock(REQUESTED_AT)
    return NotificationService(checked, clock, config or NotificationConfig()), notifier


# 确认请求和截止时刻


def test_default_deadline_is_30_minutes_later() -> None:
    """spec「默认 30 分钟的截止时刻」。"""
    service, notifier = _service()

    pending = service.request_confirmation(p1(), p1_decision())

    assert notifier.requests == [
        ConfirmationRequest(plan=p1(), decision=p1_decision(), deadline=DEADLINE)
    ]
    assert pending == PendingConfirmation(message_id="M1", deadline=DEADLINE)


def test_configured_timeout() -> None:
    """spec「改确认超时」。"""
    service, notifier = _service(NotificationConfig(confirmation_timeout=timedelta(minutes=10)))

    pending = service.request_confirmation(p1(), p1_decision())

    assert pending.deadline == datetime.fromisoformat("2026-10-08T14:10:00+00:00")
    assert notifier.requests[0].deadline == pending.deadline


def test_adjusted_plan_can_request_confirmation() -> None:
    """spec「确认请求和截止时刻」：被风控调整的方案照样请求确认。"""
    service, notifier = _service()

    service.request_confirmation(p1(), p1_decision(RiskOutcome.ADJUSTED))

    assert len(notifier.requests) == 1


def test_rejected_plan_cannot_request_confirmation() -> None:
    """spec「被风控拒绝的方案」。"""
    service, notifier = _service()

    with pytest.raises(ValueError, match="拒绝"):
        service.request_confirmation(p1(), p1_decision(RiskOutcome.REJECTED))

    assert notifier.requests == []


# 告警


def test_alert_is_passed_on() -> None:
    """spec「推送告警」：用例把文字原样交给通知通道。"""
    service, notifier = _service()

    service.alert("AAPL 没有止损单：挂单失败")

    assert notifier.alerts == ["AAPL 没有止损单：挂单失败"]


def test_alert_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """spec「告警发不出去」。"""
    service, notifier = _service()
    notifier.fail_next = True

    with caplog.at_level(logging.ERROR):
        service.alert("AAPL 没有止损单：挂单失败")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "AAPL 没有止损单：挂单失败" in errors[0].getMessage()
    assert "测试安排的失败" in errors[0].getMessage()


# 结束确认


def test_finish_is_passed_on() -> None:
    """spec「报告超时」：用例把消息标识和结果原样交给通知通道。"""
    service, notifier = _service()

    service.finish_confirmation("M1", ConfirmationOutcome.TIMED_OUT)

    assert notifier.finished == [("M1", ConfirmationOutcome.TIMED_OUT)]


def test_finish_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """spec「报告发不出去」。"""
    service, notifier = _service()
    notifier.fail_next = True

    with caplog.at_level(logging.ERROR):
        service.finish_confirmation("M1", ConfirmationOutcome.TIMED_OUT)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "M1" in errors[0].getMessage()


# 同步调用和失败


def test_request_failure_is_raised() -> None:
    """spec「请求确认失败时用例抛错」。"""
    service, notifier = _service()
    notifier.fail_next = True

    with pytest.raises(NotificationUnavailableError):
        service.request_confirmation(p1(), p1_decision())
