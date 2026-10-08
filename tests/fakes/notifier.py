"""测试用的通知通道：记录告警、确认请求和报告的结果，测试用它安排发送失败。"""

from itertools import count

from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    ConfirmationRequest,
    NotificationUnavailableError,
    PendingConfirmation,
)


class FakeNotifier:
    """满足通知模块的 Notifier。

    - alerts、requests、finished：按顺序记录每次调用；失败的调用也记录
    - 确认消息的标识依次是 M1、M2……
    - fail_next：设为真时，下一次调用抛 NotificationUnavailableError，之后恢复正常
    """

    def __init__(self) -> None:
        self.alerts: list[str] = []
        self.requests: list[ConfirmationRequest] = []
        self.finished: list[tuple[str, ConfirmationOutcome]] = []
        self.fail_next = False
        self._ids = count(1)

    def alert(self, text: str) -> None:
        self.alerts.append(text)
        self._maybe_fail()

    def request_confirmation(self, request: ConfirmationRequest) -> PendingConfirmation:
        self.requests.append(request)
        self._maybe_fail()
        return PendingConfirmation(message_id=f"M{next(self._ids)}", deadline=request.deadline)

    def finish_confirmation(self, message_id: str, outcome: ConfirmationOutcome) -> None:
        self.finished.append((message_id, outcome))
        self._maybe_fail()

    def _maybe_fail(self) -> None:
        if self.fail_next:
            self.fail_next = False
            raise NotificationUnavailableError("测试安排的失败")
