"""通知的用例：推送告警，请求确认，报告确认的结果。

见 discord-notification spec，以及 add-discord-notify 的 design D3。
"""

import logging

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.notification.config import NotificationConfig
from tradingagentspro.modules.notification.domain.confirmation import (
    ConfirmationOutcome,
    ConfirmationRequest,
    EmergencyConfirmationRequest,
    PendingConfirmation,
)
from tradingagentspro.modules.notification.ports import NotificationUnavailableError, Notifier
from tradingagentspro.shared.clock import Clock

_log = logging.getLogger(__name__)


class NotificationService:
    """通知的入口。告警和报告结果是尽力而为：发不出去时只写日志；请求确认发不出去时抛错。"""

    def __init__(self, notifier: Notifier, clock: Clock, config: NotificationConfig) -> None:
        self._notifier = notifier
        self._clock = clock
        self._config = config

    def alert(self, text: str) -> None:
        """推送告警；发不出去时写错误日志（含文字和原因），不抛错。"""
        try:
            self._notifier.alert(text)
        except NotificationUnavailableError as error:
            _log.error("告警没有发出去（%s）：%s", error, text)

    def request_confirmation(self, plan: TradePlan, decision: RiskDecision) -> PendingConfirmation:
        """请求人工确认这个方案：截止时刻 = 现在 + 确认超时。

        风控拒绝的方案抛 ValueError，不发消息；发不出去时抛 NotificationUnavailableError。
        """
        if decision.outcome is RiskOutcome.REJECTED:
            raise ValueError(f"方案 {plan.plan_id} 被风控拒绝，不能请求确认")
        deadline = self._clock.now() + self._config.confirmation_timeout
        request = ConfirmationRequest(plan=plan, decision=decision, deadline=deadline)
        return self._notifier.request_confirmation(request)

    def request_emergency_confirmation(
        self, plan: TradePlan, quantity: int, reason: str
    ) -> PendingConfirmation:
        """请求人工确认这个紧急退出：截止时刻 = 现在 + 确认超时。

        plan 是代码触发的清仓方案，quantity 是当前持仓股数，reason 是原因。发不出去时抛
        NotificationUnavailableError。
        """
        deadline = self._clock.now() + self._config.confirmation_timeout
        request = EmergencyConfirmationRequest(
            plan=plan, quantity=quantity, reason=reason, deadline=deadline
        )
        return self._notifier.request_emergency_confirmation(request)

    def finish_confirmation(self, message_id: str, outcome: ConfirmationOutcome) -> None:
        """报告确认的结果（已超时或已作废）；发不出去时写错误日志，不抛错。"""
        try:
            self._notifier.finish_confirmation(message_id, outcome)
        except NotificationUnavailableError as error:
            _log.error("确认消息 %s 没有改为%s（%s）", message_id, outcome, error)
