"""Agent 网关：agent 之间只通过它转发的 Task 和 Result 协作。

- 收到模型输出时的校验：子 agent 的输出和 PM 结论里的信号，评估窗口是否在配置的选项里、数据截至
  时刻是否晚于现在（signal-format spec）
- 任务派发：PM 和 Critic 的任务请求放行或拒绝，相同的任务复用，按案件计任务数和轮次；子 agent 的
  输出、调用失败和超时变成 Result；给出本案件的任务、用量和交给结论校验的数（task-dispatch spec）

网关不做交易判断、不下单。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.gateway.config import GatewayConfig
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
from tradingagentspro.modules.gateway.ports import TaskStore
from tradingagentspro.modules.gateway.service import TaskDispatchService

__all__ = [
    "CaseScope",
    "Dispatch",
    "DispatchAction",
    "GatewayConfig",
    "Malformed",
    "SignalRejection",
    "TaskDispatchService",
    "TaskRecord",
    "TaskStore",
    "TaskUsage",
    "call_failed",
    "completed_results",
    "conclusion_signal_rejection",
    "dispatch_rejection",
    "dispatched_agents",
    "expired",
    "new_task",
    "received",
    "rejected",
    "reusable",
    "rounds_used",
    "same_task",
    "signal_rejection",
    "tasks_used",
    "usage",
]
