"""风控的表：预留的版本表 risk_reservations，以及 ReservationStore 的数据库实现。

见 state-store spec 和 add-state-store 的 design D1。
"""

from uuid import UUID

from sqlalchemy import Boolean, Column, Engine, Uuid

from tradingagentspro.modules.risk.domain.reservation import Reservation
from tradingagentspro.modules.statestore import VersionedStore, versioned_table
from tradingagentspro.shared.clock import Clock

RESERVATIONS = versioned_table(
    "risk_reservations",
    Column("plan_id", Uuid, nullable=False),
    Column("active", Boolean, nullable=False),
)


class PostgresReservationStore:
    """满足 ReservationStore：预留存在状态库的 risk_reservations 表里，有效预留的 active 为真。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, RESERVATIONS, Reservation)

    def get(self, plan_id: UUID) -> Reservation | None:
        return self._versions.get(plan_id)

    def active(self) -> tuple[Reservation, ...]:
        return self._versions.latest(active=True)

    def save(self, reservation: Reservation) -> None:
        self._versions.save(reservation.plan.plan_id, reservation, active=reservation.is_active)
