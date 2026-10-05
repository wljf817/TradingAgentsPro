"""状态库：共享状态库在数据库里的统一写法，对应架构图的「共享状态库」节点。

每张表只追加不覆盖：内容有变化时写入一个新版本，读取时取最新版本，见 openspec 的
state-store spec。表和它的数据库实现放在拥有这张表的模块的 repo.py 里，用这里的写法定义。
模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.statestore.repo import VersionedStore, connect, versioned_table

__all__ = ["VersionedStore", "connect", "versioned_table"]
