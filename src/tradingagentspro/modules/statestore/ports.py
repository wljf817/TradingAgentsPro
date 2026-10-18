"""状态库模块需要的外部接口：快照的存储。"""

from typing import Protocol
from uuid import UUID

from tradingagentspro.contracts.snapshot import Snapshot


class SnapshotStore(Protocol):
    """快照的存储，按案件 ID 和版本只追加，数据库实现在 repo.py。错误原样抛出，不当作没有快照。"""

    def add(self, snapshot: Snapshot) -> None:
        """写入一版；这个案件已经有这一版时报错，已有的不变。"""
        ...

    def get(self, case_id: UUID, version: int) -> Snapshot | None:
        """这个案件的这一版；没有时为 None。"""
        ...

    def latest(self, case_id: UUID) -> Snapshot | None:
        """这个案件版本号最大的一版；还没有快照时为 None。"""
        ...
