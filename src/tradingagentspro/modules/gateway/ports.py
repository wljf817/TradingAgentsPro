"""Agent 网关需要的外部接口：调用模型、任务和 Result 的存储、费用的存储。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from tradingagentspro.contracts.agent_call import (
    CriticReply,
    CriticRequest,
    PmReply,
    PmRequest,
    RetrospectiveReport,
    ReviewerRequest,
    SubAgentRequest,
)
from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.contracts.review import PlanResponse
from tradingagentspro.contracts.task import Result, Task
from tradingagentspro.modules.gateway.domain.budget import CallCost
from tradingagentspro.modules.gateway.domain.dispatch import TaskRecord
from tradingagentspro.modules.gateway.domain.fallback import CallError


class ModelCallError(RuntimeError):
    """一次尝试没有拿到输出：调用错误，和厂商给出的这次的用量（拿不到时为 None）。"""

    def __init__(self, error: CallError, usage: TokenUsage | None = None) -> None:
        super().__init__(error.value)
        self.error = error
        self.usage = usage


@dataclass(frozen=True, slots=True)
class ModelReply[T]:
    """一次拿到输出的尝试：输出和这次的用量。"""

    output: T
    usage: TokenUsage


class ModelBackend(Protocol):
    """调用模型：每个方法向 model 发一次请求，就是 model-fallback spec 的一次尝试。

    超过 timeout 仍没有输出、或者没有拿到输出时抛 ModelCallError，不重试、不换模型；重试、切换
    备用模型和记费用由 AgentCallService 做。PM、Critic、Reviewer 的输出是按 contracts 校验过的
    模型；子 agent 的输出是原样的文本，由网关按 task-dispatch spec 校验。见 agent-calls spec 和
    add-stand-ins 的 design D1、D4。
    """

    def pm(self, model: ModelRef, timeout: timedelta, request: PmRequest) -> ModelReply[PmReply]:
        """PM 的研究：派发任务或提交结论。"""
        ...

    def pm_respond(
        self, model: ModelRef, timeout: timedelta, request: PmRequest
    ) -> ModelReply[PlanResponse]:
        """PM 回应对话最后一轮交回的质询。"""
        ...

    def critic(
        self, model: ModelRef, timeout: timedelta, request: CriticRequest
    ) -> ModelReply[CriticReply]:
        """Critic：补充研究或审核。"""
        ...

    def sub_agent(
        self, model: ModelRef, timeout: timedelta, request: SubAgentRequest
    ) -> ModelReply[str]:
        """Task 的目标子 agent：信号或无信号的 JSON 文本。"""
        ...

    def reviewer(
        self, model: ModelRef, timeout: timedelta, request: ReviewerRequest
    ) -> ModelReply[RetrospectiveReport]:
        """Reviewer 的复盘报告。"""
        ...


class TaskStore(Protocol):
    """任务和 Result 的存储。只追加：Task 生成时写入，Result 出来时写入，写入以后都不再改。

    网关是 Task 和 Result 唯一的写入者；数据库实现是 repo 的 PostgresTaskStore（表 gateway_tasks、
    gateway_results），见 task-dispatch spec「任务的存储」。
    """

    def case_records(self, case_id: UUID) -> tuple[TaskRecord, ...]:
        """本案件的任务和它们的 Result，按 Task 写入的先后。"""
        ...

    def record(self, task_id: UUID) -> TaskRecord:
        """一个任务和它的 Result；没有这个任务时抛 KeyError。"""
        ...

    def add_task(self, task: Task) -> None:
        """写入一个新 Task。"""
        ...

    def add_result(self, result: Result) -> Result | None:
        """写入一个 Task 的 Result；这个 Task 已经写入。

        这个 Task 已经有 Result 时不写入，给出已有的那个（先写入的为准）；写入了给出 None。几个调用
        同时写同一个 Task 的 Result 时也只有一个写入。
        """
        ...


class CostStore(Protocol):
    """模型调用费用的存储。只追加：每次尝试写入一条，写入以后不改；同时写入的每一条都写进去。

    网关是唯一的写入者；数据库实现是 repo 的 PostgresCostStore（表 gateway_costs），见 call-budget
    spec「费用的存储」。
    """

    def since(self, moment: datetime) -> tuple[CallCost, ...]:
        """记下的时刻不早于 moment 的记录，按写入的先后。"""
        ...

    def case_costs(self, case_id: UUID) -> tuple[CallCost, ...]:
        """这个案件的全部记录，按写入的先后。"""
        ...

    def add(self, entry: CallCost) -> None:
        """写入一条记录。"""
        ...
