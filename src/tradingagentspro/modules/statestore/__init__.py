"""状态库：共享状态库在数据库里的统一写法，以及案件的快照。对应架构图的「共享状态库」节点。

每张表只追加不覆盖：内容有变化时写入一个新版本，读取时取最新版本，见 openspec 的
state-store spec。表和它的数据库实现放在拥有这张表的模块的 repo.py 里，用这里的写法定义。

快照是案件开始时复制下来的、本案件 agent 要读的事实（格式在 contracts.snapshot），见
state-snapshot spec：这个模块存它、按版本读它、按条目 ID 查条目，每存一版写一条程序日志。快照存在
statestore_snapshots 表里，只由这个模块写入，数据库实现是 PostgresSnapshotStore。组出
快照要读很多模块，在 workflows 里做。这个模块只依赖 contracts、shared 和程序日志，所有带表的
模块都 import 它，不会成环。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.statestore.ports import SnapshotStore
from tradingagentspro.modules.statestore.repo import (
    PostgresSnapshotStore,
    VersionedStore,
    connect,
    versioned_table,
)
from tradingagentspro.modules.statestore.service import SnapshotService

__all__ = [
    "PostgresSnapshotStore",
    "SnapshotService",
    "SnapshotStore",
    "VersionedStore",
    "connect",
    "versioned_table",
]
