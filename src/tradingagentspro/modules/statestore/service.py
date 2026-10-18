"""快照的用例：保存一版并写程序日志，按版本读，读最新的一版，按条目 ID 查条目。

见 state-snapshot spec「读取快照和查条目」「快照的存储」、program-log spec「调度器的动作」和
add-state-snapshot 的 design D6、D10。
"""

from uuid import UUID

from tradingagentspro.contracts.snapshot import Snapshot, SnapshotEntry, entry_version
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
from tradingagentspro.modules.statestore.ports import SnapshotStore


class SnapshotService:
    """快照的入口。不拿锁：同一个案件的快照由它的案件工作流一版一版生成。

    存储和程序日志的错误原样抛出；写程序日志出错时，已经保存的快照不撤回。
    """

    def __init__(self, store: SnapshotStore, log: ProgramLog) -> None:
        self._store = store
        self._log = log

    def save(self, snapshot: Snapshot) -> None:
        """写入一版，再写一条生成快照的程序日志。这一版已经存在时报错，不写程序日志。"""
        self._store.add(snapshot)
        ids = [e.entry_id for e in snapshot.entries()]
        stock = snapshot.symbol or "不针对单只股票"
        reason = snapshot.reason or "案件开始"
        self._log.write(
            Actor.SCHEDULER,
            Action.TAKE_SNAPSHOT,
            f"s{snapshot.version}",
            f"{stock}，{reason}，{len(ids)} 个条目",
            outputs=ids,
            case_id=snapshot.case_id,
        )

    def snapshot(self, case_id: UUID, version: int) -> Snapshot | None:
        """这个案件的这一版；没有时为 None。"""
        return self._store.get(case_id, version)

    def latest(self, case_id: UUID) -> Snapshot | None:
        """这个案件版本号最大的一版；还没有快照时为 None。"""
        return self._store.latest(case_id)

    def resolve(self, case_id: UUID, entry_id: str) -> SnapshotEntry | None:
        """按条目 ID 的前缀找到那一版，在里面找这个条目；找不到时为 None。"""
        version = entry_version(entry_id)
        if version is None:
            return None
        found = self._store.get(case_id, version)
        return None if found is None else found.entry(entry_id)
