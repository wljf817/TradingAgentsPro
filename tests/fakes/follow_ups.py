"""测试用的跟进存储：存在内存里，按跟进 ID 保存每个跟进最新的记录，包括不再待执行的。"""

from datetime import datetime
from uuid import UUID

from tradingagentspro.modules.followups import FollowUp, FollowUpStatus


class InMemoryFollowUpStore:
    """满足跟进模块的 FollowUpStore。get 只给测试用，用来查不再待执行的跟进。"""

    def __init__(self) -> None:
        self._follow_ups: dict[UUID, FollowUp] = {}

    def pending(self) -> tuple[FollowUp, ...]:
        return tuple(f for f in self._follow_ups.values() if f.status is FollowUpStatus.PENDING)

    def requested_since(self, moment: datetime) -> tuple[FollowUp, ...]:
        return tuple(f for f in self._follow_ups.values() if f.requested_at >= moment)

    def save(self, follow_up: FollowUp) -> None:
        self._follow_ups[follow_up.follow_up_id] = follow_up

    def get(self, follow_up_id: UUID) -> FollowUp:
        return self._follow_ups[follow_up_id]
