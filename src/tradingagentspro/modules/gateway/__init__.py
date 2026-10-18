"""Agent 网关：agent 之间只通过它转发的 Task 和 Result 协作。

- 收到模型输出时的校验：子 agent 的输出和 PM 结论里的信号，评估窗口是否在配置的选项里、数据截至
  时刻是否晚于现在（signal-format spec）
- 任务派发：PM 和 Critic 的任务请求放行或拒绝，相同的任务复用，按案件计任务数和轮次；子 agent 的
  输出、调用失败和超时变成 Result；给出本案件的任务、用量和交给结论校验的数（task-dispatch spec）。
  Task 和 Result 存在 gateway_tasks、gateway_results 表里（PostgresTaskStore），一个任务只有一个
  Result 由 Result 表的主键保证；每个 Task 和 Result 写一条程序日志（program-log spec「Agent 网关的
  动作」）
- 备用模型：各 agent 的模型配置和厂商校验；主模型不可用时按顺序切换备用模型，案件内不切回，按模型
  熔断；给出实际使用的模型、切换的事件、PM 和 Critic 是否还有可用的模型，以及 PM 用备用模型时不能
  提的决定（model-fallback spec）
- 调用预算：模型的单价；记下每次尝试的费用，按记之前普通预算是否用完整笔记到普通预算、持仓保留预算
  或寻股预算；按现在归属的交易日给出三个预算今天的费用和用完的预算，不因为预算用完拒绝任务或调用；
  按案件给出费用记录（call-budget spec）。费用记录存在 gateway_costs 表里（PostgresCostStore）
- 调用 agent：AgentCallService 经 ModelBackend 端口向模型逐次尝试，按备用模型的步骤重试和切换，
  每次拿到用量就记一条费用；PM、Critic、Reviewer 给出输出或全部不可用，子 agent 的任务直接生成
  Result（agent-calls spec）。M4 由 integrations/standin 的替身实现端口，M5 换成真实模型

网关不做交易判断、不下单。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.gateway.config import (
    AgentModels,
    GatewayConfig,
    ModelAssignments,
    ModelPrice,
)
from tradingagentspro.modules.gateway.domain.budget import (
    Budget,
    BudgetStatus,
    CallCost,
    budget_status,
    charged_budget,
    cost_of,
)
from tradingagentspro.modules.gateway.domain.dispatch import (
    CaseScope,
    Dispatch,
    DispatchAction,
    TaskRecord,
    TaskUsage,
    completed_results,
    dispatch_rejection,
    dispatched_agents,
    new_task,
    reusable,
    rounds_used,
    same_task,
    tasks_used,
    usage,
)
from tradingagentspro.modules.gateway.domain.fallback import (
    AllUnavailable,
    Attempt,
    Breaker,
    CallError,
    CallStep,
    DeadlineReached,
    ModelCall,
    ModelSwitch,
    Unavailable,
    after_failure,
    attempt_timeout,
    blocked_decision,
    exhausted,
    start,
)
from tradingagentspro.modules.gateway.domain.result import (
    Malformed,
    call_failed,
    expired,
    received,
    rejected,
)
from tradingagentspro.modules.gateway.domain.signal import (
    SignalRejection,
    conclusion_signal_rejection,
    signal_rejection,
)
from tradingagentspro.modules.gateway.ports import (
    CostStore,
    ModelBackend,
    ModelCallError,
    ModelReply,
    TaskStore,
)
from tradingagentspro.modules.gateway.repo import PostgresCostStore, PostgresTaskStore
from tradingagentspro.modules.gateway.service import (
    AgentCallService,
    Answered,
    CallBudgetService,
    ModelFallbackService,
    TaskDispatchService,
)

__all__ = [
    "AgentCallService",
    "AgentModels",
    "AllUnavailable",
    "Answered",
    "Attempt",
    "Breaker",
    "Budget",
    "BudgetStatus",
    "CallBudgetService",
    "CallCost",
    "CallError",
    "CallStep",
    "CaseScope",
    "CostStore",
    "DeadlineReached",
    "Dispatch",
    "DispatchAction",
    "GatewayConfig",
    "Malformed",
    "ModelAssignments",
    "ModelBackend",
    "ModelCall",
    "ModelCallError",
    "ModelFallbackService",
    "ModelPrice",
    "ModelReply",
    "ModelSwitch",
    "PostgresCostStore",
    "PostgresTaskStore",
    "SignalRejection",
    "TaskDispatchService",
    "TaskRecord",
    "TaskStore",
    "TaskUsage",
    "Unavailable",
    "after_failure",
    "attempt_timeout",
    "blocked_decision",
    "budget_status",
    "call_failed",
    "charged_budget",
    "completed_results",
    "conclusion_signal_rejection",
    "cost_of",
    "dispatch_rejection",
    "dispatched_agents",
    "exhausted",
    "expired",
    "new_task",
    "received",
    "rejected",
    "reusable",
    "rounds_used",
    "same_task",
    "signal_rejection",
    "start",
    "tasks_used",
    "usage",
]
