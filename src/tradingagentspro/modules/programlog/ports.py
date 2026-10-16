"""程序日志模块需要的外部接口：条目的存储。"""

from collections.abc import Collection
from typing import Protocol

from tradingagentspro.modules.programlog.domain.entry import Action, LogEntry


class ProgramLogStore(Protocol):
    """条目的存储：只追加，没有修改和删除，见 program-log spec「只追加」。

    数据库的错误原样抛出，不当作已经写入。
    """

    def append(self, entry: LogEntry) -> None:
        """追加一条，编号比之前的都大。"""
        ...

    def latest(self, actions: Collection[Action]) -> LogEntry | None:
        """这几种动作里最近写入（编号最大）的一条；没有时为 None。"""
        ...
