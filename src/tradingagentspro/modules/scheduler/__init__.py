"""调度器：按触发和开始运行时的股票状态确定案件类型，按案件类型校验 PM 提交的结论，按一份快照
决定启动哪些排队的案件、作废哪些方案，以及紧急退出时中止哪个案件；用案件状态推进一个案件的阶段。

排队的案件存在 scheduler_queue、scheduler_queue_exits 两张表里（QueueService、PostgresQueueStore），
见 queue-runner spec「排队」「排队的案件的存储」。

调度器不调用模型、不做交易判断、不下单。规则见 openspec 的 case-types spec、case-queue spec 和
case-stages spec。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.scheduler.config import SchedulerConfig
from tradingagentspro.modules.scheduler.domain.case_type import (
    CaseDefinition,
    EventTrigger,
    FollowUpTrigger,
    HeartbeatTrigger,
    SellTrigger,
    Trigger,
    conclusion_model,
    define_case,
)
from tradingagentspro.modules.scheduler.domain.queue import (
    CaseStart,
    EmergencyDecision,
    PendingPlan,
    PlanStage,
    QueuedCase,
    QueueDecision,
    QueueExit,
    QueueSnapshot,
    RunningCase,
    emergency_decision,
    queue_decision,
)
from tradingagentspro.modules.scheduler.domain.stage import (
    CaseStage,
    CaseState,
    CriticOutcome,
    new_case,
)
from tradingagentspro.modules.scheduler.domain.validation import (
    ConclusionRejection,
    conclusion_rejection,
)
from tradingagentspro.modules.scheduler.ports import QueueStore
from tradingagentspro.modules.scheduler.repo import PostgresQueueStore
from tradingagentspro.modules.scheduler.service import QueueService

__all__ = [
    "CaseDefinition",
    "CaseStage",
    "CaseStart",
    "CaseState",
    "ConclusionRejection",
    "CriticOutcome",
    "EmergencyDecision",
    "EventTrigger",
    "FollowUpTrigger",
    "HeartbeatTrigger",
    "PendingPlan",
    "PlanStage",
    "PostgresQueueStore",
    "QueueDecision",
    "QueueExit",
    "QueueService",
    "QueueSnapshot",
    "QueueStore",
    "QueuedCase",
    "RunningCase",
    "SchedulerConfig",
    "SellTrigger",
    "Trigger",
    "conclusion_model",
    "conclusion_rejection",
    "define_case",
    "emergency_decision",
    "new_case",
    "queue_decision",
]
