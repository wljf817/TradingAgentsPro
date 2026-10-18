"""案件记录：每个案件（三种驱动触发的，包括排队以后没有运行的，以及复盘案件）在进行和结束时的
事实——触发、案件类型、结论和给出它的模型、交易方案和它的审核、回应与风控结果、股票池增删的发现、
结束原因、Task 数和调用次数。规则见 openspec 的 case-records spec。

- 记录存在状态库所在数据库的三张表里，只由这个模块写入：案件表 caserecords_cases、方案表
  caserecords_proposals（按案件 ID 只追加版本）、发现表 caserecords_findings（一项一行）
- 写入方是「案件工作流」，排队的案件不再运行由「排队运行」写；每一步只写一次，同一步再写时记录不变
- 案件的阶段只在调度器的案件状态里，案件记录不记阶段，也不拿来推进案件或排队
- 写案件记录不写程序日志：调度器的动作由做这些动作的流程写
- 读取：按案件 ID；一只股票最近结束的案件；某种案件类型最近一次得出结论的案件

模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.caserecords.domain.record import (
    CaseConclusion,
    CaseRecord,
    CaseStatus,
    CaseTrigger,
    Finding,
    FindingKind,
    ProposalRecord,
    Response,
    Review,
    TriggerKind,
    concluded,
    ended,
    left_queue,
    proposed,
    require_started,
    responded,
    reviewed,
    started,
    with_risk,
    without_critic,
)
from tradingagentspro.modules.caserecords.ports import CaseRecordStore
from tradingagentspro.modules.caserecords.repo import PostgresCaseRecordStore
from tradingagentspro.modules.caserecords.service import CaseRecordService

__all__ = [
    "CaseConclusion",
    "CaseRecord",
    "CaseRecordService",
    "CaseRecordStore",
    "CaseStatus",
    "CaseTrigger",
    "Finding",
    "FindingKind",
    "PostgresCaseRecordStore",
    "ProposalRecord",
    "Response",
    "Review",
    "TriggerKind",
    "concluded",
    "ended",
    "left_queue",
    "proposed",
    "require_started",
    "responded",
    "reviewed",
    "started",
    "with_risk",
    "without_critic",
]
