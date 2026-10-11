"""通知模块需要的外部接口：通知通道，人按下按钮以后的回调，以及人手工录入的方案交给谁。"""

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.notification.domain.confirmation import (
    ConfirmationOutcome,
    ConfirmationRequest,
    EmergencyConfirmationRequest,
    PendingConfirmation,
)


class NotificationUnavailableError(RuntimeError):
    """通知通道超时、断线、报错，或者还没启动、已经关闭：这次没有发出去。"""


type DecisionHandler = Callable[[UUID, ConfirmationOutcome], None]
"""人按下按钮以后的回调：方案 ID 和 CONFIRMED 或 REJECTED。

由 bootstrap 实现、通知通道调用。在通知通道的工作线程里调用，可以阻塞；抛错表示没有送达，
通知通道会请人再按一次。只有这个方案还在等确认时才算送达，否则要抛错。
"""


class Notifier(Protocol):
    """通知通道。每个方法都可以从任何线程同步调用，失败时抛 NotificationUnavailableError。"""

    def alert(self, text: str) -> None:
        """推送一条告警。"""
        ...

    def request_confirmation(self, request: ConfirmationRequest) -> PendingConfirmation:
        """发出确认消息；同一个方案重复请求时返回第一次的那条。"""
        ...

    def request_emergency_confirmation(
        self, request: EmergencyConfirmationRequest
    ) -> PendingConfirmation:
        """发出紧急退出的确认消息；同一个方案重复请求时返回第一次的那条。"""
        ...

    def finish_confirmation(self, message_id: str, outcome: ConfirmationOutcome) -> None:
        """把确认消息改为这个结果、去掉按钮；消息已被删掉时什么也不做。"""
        ...


class PlanIntake(Protocol):
    """人手工录入的方案交给谁：由 bootstrap 实现，通知通道调用。见 manual-plans spec「提交接口」。

    两个方法都在通知通道的工作线程里调用，可以阻塞。
    """

    def held_sector(self, symbol: str) -> str | None:
        """这只股票持仓的板块；没有持仓时为 None。减仓和清仓的方案用它填板块。"""
        ...

    def submit(self, plan: TradePlan) -> RiskDecision:
        """提交方案，风控得出结果就返回，不等人工确认。

        通过或调整的方案由实现方请求人工确认。抛错表示没有拿到风控结果：方案可能已经
        提交，也可能没有。
        """
        ...
