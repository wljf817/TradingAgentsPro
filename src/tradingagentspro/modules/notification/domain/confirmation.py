"""确认请求、案件的意见、确认的结果和发出的确认消息，见 discord-notification spec
「确认请求和截止时刻」「确认消息」「紧急退出的确认消息」。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tradingagentspro.contracts.review import CriticReview, PlanResponse
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
class Disputed:
    """Critic 有分歧：确认消息附上双方的意见，见 discord-notification spec「确认消息」。"""

    rationale: str  # PM 的方案理由：结论的理由
    reviews: tuple[CriticReview, ...]  # Critic 的每次审核，按先后
    responses: tuple[PlanResponse, ...]  # PM 的每轮回应，按先后


@dataclass(frozen=True, slots=True)
class Unreviewed:
    """Critic 不可用，方案没有经过审核。"""


# 案件的方案在确认消息里附上的意见；Critic 通过和 /plan 的方案没有，见 order-flow spec
# 「请求人工确认」
type CaseNote = Disputed | Unreviewed


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    """请求确认的一个方案、它的风控结果和案件的意见（没有时为 None）。

    deadline 是 UTC 时刻，由 NotificationService 算出。
    """

    plan: TradePlan
    decision: RiskDecision
    deadline: datetime
    note: CaseNote | None


@dataclass(frozen=True, slots=True)
class EmergencyConfirmationRequest:
    """请求确认的一个紧急退出：代码触发的清仓方案、当前持仓股数和原因。

    deadline 是 UTC 时刻，由 NotificationService 算出。
    """

    plan: TradePlan
    quantity: int
    reason: str
    deadline: datetime


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    """发出的确认消息。message_id 由通知通道给出，调用方报告结果时原样传回；deadline 是这条
    消息上的截止时刻。"""

    message_id: str
    deadline: datetime
