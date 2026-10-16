"""程序日志：按时间先后记下所有模块、agent 和人的每个动作，只追加，不修改、不删除。

对应架构图「记录与复盘」里的程序日志，规则见 openspec 的 program-log spec。条目存在状态库所在数据库
的 programlog_entries 表里，只由这个模块写入。别的模块、流程和启动程序在构造参数里拿一个
ProgramLog（由 bootstrap 注入），import 这里导出的名字来写：这个模块只依赖 shared，谁 import
它都不会成环，见 add-program-log 的 design D3。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.programlog.domain.entry import (
    ACCOUNT,
    WHOLE_SYSTEM,
    Action,
    Actor,
    LogEntry,
)
from tradingagentspro.modules.programlog.ports import ProgramLogStore
from tradingagentspro.modules.programlog.repo import PostgresProgramLogStore
from tradingagentspro.modules.programlog.service import ProgramLog

__all__ = [
    "ACCOUNT",
    "WHOLE_SYSTEM",
    "Action",
    "Actor",
    "LogEntry",
    "PostgresProgramLogStore",
    "ProgramLog",
    "ProgramLogStore",
]
