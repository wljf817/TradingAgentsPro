"""测试用的预留存储：存在内存里，按方案 ID 保存每个方案预留的最新状态。"""

from uuid import UUID

from tradingagentspro.modules.risk import Reservation


class InMemoryReservationStore:
    """满足风控模块的 ReservationStore。用同一个实例重新创建风控用例，相当于进程重启。"""

    def __init__(self) -> None:
        self._reservations: dict[UUID, Reservation] = {}

    def get(self, plan_id: UUID) -> Reservation | None:
        return self._reservations.get(plan_id)

    def active(self) -> tuple[Reservation, ...]:
        return tuple(r for r in self._reservations.values() if r.is_active)

    def save(self, reservation: Reservation) -> None:
        self._reservations[reservation.plan.plan_id] = reservation
