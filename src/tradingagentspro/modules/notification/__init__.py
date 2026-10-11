"""通知：推送告警，请求人工确认交易方案，报告确认的结果。对应架构图「风控与执行」里的「人工确认」。

规则见 openspec 的 discord-notification spec；哪些方案要人工确认见 confirmation-rules spec。
通知通道和按钮的回调由 bootstrap 注入。
人通过通知通道手工录入的方案交给 PlanIntake，见 manual-plans spec。
模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.notification.config import NotificationConfig
from tradingagentspro.modules.notification.domain.confirmation import (
    ConfirmationOutcome,
    ConfirmationRequest,
    EmergencyConfirmationRequest,
    PendingConfirmation,
)
from tradingagentspro.modules.notification.domain.rules import (
    CaseConfirmation,
    case_plan_confirmation,
)
from tradingagentspro.modules.notification.ports import (
    DecisionHandler,
    NotificationUnavailableError,
    Notifier,
    PlanIntake,
)
from tradingagentspro.modules.notification.service import NotificationService

__all__ = [
    "CaseConfirmation",
    "ConfirmationOutcome",
    "ConfirmationRequest",
    "DecisionHandler",
    "EmergencyConfirmationRequest",
    "NotificationConfig",
    "NotificationService",
    "NotificationUnavailableError",
    "Notifier",
    "PendingConfirmation",
    "PlanIntake",
    "case_plan_confirmation",
]
