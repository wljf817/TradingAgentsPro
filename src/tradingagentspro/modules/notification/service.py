"""通知的用例：推送告警，请求确认，报告确认的结果；每一次都写进程序日志。

见 discord-notification spec、program-log spec「系统的动作」，以及 add-discord-notify 的 design D3、
add-program-log 的 design D8。
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

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
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
from tradingagentspro.shared.clock import Clock

_log = logging.getLogger(__name__)

# 结束确认的条目里写的结果
_OUTCOMES = {
    ConfirmationOutcome.CONFIRMED: "已确认",
    ConfirmationOutcome.REJECTED: "已拒绝",
    ConfirmationOutcome.TIMED_OUT: "已超时",
    ConfirmationOutcome.VOIDED: "已作废",
}


class NotificationService:
    """通知的入口。告警和报告结果是尽力而为：发不出去时只写日志；请求确认发不出去时抛错。

    推送告警、请求确认和报告结果都写一条程序日志，发不出去的也写，条目里写明原因。
    """

    def __init__(
        self, notifier: Notifier, clock: Clock, config: NotificationConfig, log: ProgramLog
    ) -> None:
        self._notifier = notifier
        self._clock = clock
        self._config = config
        self._program_log = log

    def alert(self, subject: str, text: str) -> None:
        """推送告警，subject 是告警针对的对象；写程序日志的告警。

        发不出去时写错误日志（含文字和原因），条目写明没有发出去，不抛错。
        """
        try:
            self._notifier.alert(text)
        except NotificationUnavailableError as error:
            _log.error("告警没有发出去（%s）：%s", error, text)
            detail = f"{text}\n没有发出去：{error}"
        else:
            detail = text
        self._program_log.write(Actor.SYSTEM, Action.ALERT, subject, detail)

    def request_confirmation(self, plan: TradePlan, decision: RiskDecision) -> PendingConfirmation:
        """请求人工确认这个方案：截止时刻 = 现在 + 确认超时；写程序日志的请求确认。

        风控拒绝的方案抛 ValueError，不发消息；发不出去时条目写明原因，再抛
        NotificationUnavailableError。
        """
        if decision.outcome is RiskOutcome.REJECTED:
            raise ValueError(f"方案 {plan.plan_id} 被风控拒绝，不能请求确认")
        deadline = self._clock.now() + self._config.confirmation_timeout
        request = ConfirmationRequest(plan=plan, decision=decision, deadline=deadline)
        return self._request(plan, (), lambda: self._notifier.request_confirmation(request))

    def request_emergency_confirmation(
        self, plan: TradePlan, quantity: int, reason: str
    ) -> PendingConfirmation:
        """请求人工确认这个紧急退出：截止时刻 = 现在 + 确认超时；写程序日志的请求确认。

        plan 是代码触发的清仓方案，quantity 是当前持仓股数，reason 是原因。发不出去时条目写明
        原因，再抛 NotificationUnavailableError。
        """
        deadline = self._clock.now() + self._config.confirmation_timeout
        request = EmergencyConfirmationRequest(
            plan=plan, quantity=quantity, reason=reason, deadline=deadline
        )
        about = (f"紧急退出 {plan.symbol}：{reason}", f"当前持仓 {quantity} 股")
        return self._request(
            plan, about, lambda: self._notifier.request_emergency_confirmation(request)
        )

    def finish_confirmation(
        self, plan_id: UUID, message_id: str, outcome: ConfirmationOutcome
    ) -> None:
        """报告这个方案的确认消息的结果（已超时或已作废）；写程序日志的结束确认。

        发不出去时写错误日志，条目写明没有改成，不抛错。
        """
        detail = _OUTCOMES[outcome]
        try:
            self._notifier.finish_confirmation(message_id, outcome)
        except NotificationUnavailableError as error:
            _log.error("确认消息 %s 没有改为%s（%s）", message_id, outcome, error)
            detail += f"；没有改成：{error}"
        self._program_log.write(
            Actor.SYSTEM, Action.FINISH_CONFIRMATION, str(plan_id), detail, inputs=[message_id]
        )

    def _request(
        self,
        plan: TradePlan,
        about: tuple[str, ...],
        send: Callable[[], PendingConfirmation],
    ) -> PendingConfirmation:
        """发出确认消息，写程序日志的请求确认；about 是说明开头的几段。发不出去时照样写，再抛。"""
        subject = str(plan.plan_id)
        try:
            pending = send()
        except NotificationUnavailableError as error:
            detail = "；".join([*about, f"没有发出去：{error}"])
            self._program_log.write(Actor.SYSTEM, Action.REQUEST_CONFIRMATION, subject, detail)
            raise
        detail = "；".join([*about, f"截止 {_utc(pending.deadline)}"])
        self._program_log.write(
            Actor.SYSTEM,
            Action.REQUEST_CONFIRMATION,
            subject,
            detail,
            outputs=[pending.message_id],
        )
        return pending


def _utc(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
