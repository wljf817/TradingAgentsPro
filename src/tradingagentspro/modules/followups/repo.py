"""跟进的表：跟进的版本表 followups_follow_ups，以及 FollowUpStore 的数据库实现。

见 state-store spec、follow-ups spec「跟进的存储」和 update-follow-up-storage 的 design D1、D2。
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Engine, Uuid

from tradingagentspro.modules.followups.domain.follow_up import FollowUp, FollowUpStatus
from tradingagentspro.modules.statestore import VersionedStore, versioned_table
from tradingagentspro.shared.clock import Clock

FOLLOW_UPS = versioned_table(
    "followups_follow_ups",
    Column("follow_up_id", Uuid, nullable=False),
    Column("pending", Boolean, nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
)


class PostgresFollowUpStore:
    """满足 FollowUpStore：跟进存在状态库的 followups_follow_ups 表里，键是跟进 ID。

    待执行的跟进 pending 为真；requested_at 是获批时刻，每个版本都一样，用来列出当天获批的跟进。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, FOLLOW_UPS, FollowUp)

    def pending(self) -> tuple[FollowUp, ...]:
        return self._versions.latest(pending=True)

    def requested_since(self, moment: datetime) -> tuple[FollowUp, ...]:
        return self._versions.latest_since(moment, "requested_at")

    def save(self, follow_up: FollowUp) -> None:
        self._versions.save(
            follow_up.follow_up_id,
            follow_up,
            pending=follow_up.status is FollowUpStatus.PENDING,
            requested_at=follow_up.requested_at,
        )
