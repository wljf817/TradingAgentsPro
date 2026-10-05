"""风控模块需要的外部接口。"""

from typing import Protocol
from uuid import UUID

from tradingagentspro.modules.risk.domain.reservation import Reservation


class ReservationStore(Protocol):
    """预留的存储：按方案 ID 保存每个方案预留的最新状态。

    数据库实现只追加不覆盖：内容有变化时写入一个新版本，读取时取每个方案的最新版本，
    见 state-store spec。
    """

    def get(self, plan_id: UUID) -> Reservation | None:
        """这个方案的预留，包括已经结束的；没有预留时为 None。"""
        ...

    def active(self) -> tuple[Reservation, ...]:
        """全部有效预留。"""
        ...

    def save(self, reservation: Reservation) -> None:
        """保存这个方案预留的最新状态。"""
        ...
