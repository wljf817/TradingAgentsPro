"""调度器：按触发和开始运行时的股票状态确定案件类型，按案件类型校验 PM 提交的结论，并按一份快照
决定启动哪些排队的案件、作废哪些方案，以及紧急退出时中止哪个案件。

调度器不调用模型、不做交易判断、不下单。规则见 openspec 的 case-types spec 和 case-queue spec。
模块之外的代码只能 import 这里导出的名字。
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
    QueueSnapshot,
    RunningCase,
    emergency_decision,
    queue_decision,
)
from tradingagentspro.modules.scheduler.domain.validation import (
    ConclusionRejection,
    conclusion_rejection,
)

__all__ = [
    "CaseDefinition",
    "CaseStart",
    "ConclusionRejection",
    "EmergencyDecision",
    "EventTrigger",
    "FollowUpTrigger",
    "HeartbeatTrigger",
    "PendingPlan",
    "PlanStage",
    "QueueDecision",
    "QueueSnapshot",
    "QueuedCase",
    "RunningCase",
    "SchedulerConfig",
    "SellTrigger",
    "Trigger",
    "conclusion_model",
    "conclusion_rejection",
    "define_case",
    "emergency_decision",
    "queue_decision",
]
