"""确认请求、确认的结果和发出的确认消息，见 discord-notification spec「确认请求和截止时刻」。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan


class ConfirmationOutcome(StrEnum):
    """一个确认的结果。

    按钮只给出 CONFIRMED 和 REJECTED；TIMED_OUT 和 VOIDED 由调用方报告，截止以后按下的按钮
    也显示为 TIMED_OUT。
    """

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    VOIDED = "voided"


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    """请求确认的一个方案和它的风控结果。deadline 是 UTC 时刻，由 NotificationService 算出。"""

    plan: TradePlan
    decision: RiskDecision
    deadline: datetime


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    """发出的确认消息。message_id 由通知通道给出，调用方报告结果时原样传回；deadline 是这条
    消息上的截止时刻。"""

    message_id: str
    deadline: datetime
