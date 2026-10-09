"""派发规则：相同的任务、复用、放行或拒绝，以及本案件的用量和给结论校验的数。

见 task-dispatch spec「派发的顺序」「相同的任务」「案件的股票」「寻股派给谁」「PM 的轮次」
「PM 的任务数」「Critic 的补充研究额度」「本案件的任务和用量」。拒绝是发起者请求的正常结果，所以
作为返回值，不抛异常；被拒绝的请求也生成 Task，带着被拒绝的 Result 交回发起者。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.task import (
    Dispatcher,
    Result,
    ResultStatus,
    Task,
    TaskRejection,
    TaskRequest,
)
from tradingagentspro.modules.gateway.config import GatewayConfig

# 寻股只能派给的子 agent；可以复用的 Result 的状态
_EXPLORATION_AGENTS: Final = frozenset({SubAgent.QUANT, SubAgent.FUNDAMENTALS})
_REUSABLE: Final = frozenset({ResultStatus.COMPLETED, ResultStatus.NO_SIGNAL})


@dataclass(frozen=True, slots=True)
class CaseScope:
    """派发任务的案件。symbol 是案件的股票，寻股没有。"""

    case_id: UUID
    case_type: CaseType
    symbol: str | None


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """一个任务和它的 Result；还在运行时没有 Result。"""

    task: Task
    result: Result | None = None

    @property
    def dispatched(self) -> bool:
        """派发出去了：不是被拒绝的任务。"""
        return self.result is None or self.result.status is not ResultStatus.REJECTED


class DispatchAction(StrEnum):
    """一个请求的处理结果，见 spec「派发的顺序」。"""

    DISPATCHED = "dispatched"  # 派发：生成 Task，由调用方交给目标子 agent
    REUSED = "reused"  # 复用：本案件可以复用的相同任务
    REJECTED = "rejected"  # 被拒绝：生成 Task 和被拒绝的 Result


@dataclass(frozen=True, slots=True)
class Dispatch:
    """一个请求的处理结果：派发或被拒绝时是新生成的 Task，复用时是被复用的 Task。"""

    task: Task
    action: DispatchAction


@dataclass(frozen=True, slots=True)
class TaskUsage:
    """一个案件用掉的轮次和任务数。"""

    pm_rounds: int  # PM 用掉的轮次
    pm_tasks: int  # PM 派发出去的任务数
    critic_tasks: int  # Critic 用掉的补充研究额度


def same_task(request: TaskRequest, task: Task) -> bool:
    """任务类型、股票、问题相同，输入不论顺序相同；不论发起者。"""
    return (
        request.task_type is task.task_type
        and request.symbol == task.symbol
        and request.question == task.question
        and set(request.inputs) == set(task.inputs)
    )


def reusable(request: TaskRequest, records: Iterable[TaskRecord]) -> Task | None:
    """本案件可以复用的相同任务：还没有 Result，或者 Result 是完成、无信号；没有时为 None。

    同一个案件里可以复用的相同任务最多一个：有了它，后面相同的请求都复用它，不会再派。
    """
    for record in records:
        if same_task(request, record.task) and (
            record.result is None or record.result.status in _REUSABLE
        ):
            return record.task
    return None


def dispatch_rejection(
    request: TaskRequest,
    dispatcher: Dispatcher,
    scope: CaseScope,
    records: Sequence[TaskRecord],
    batch_round: int,
    config: GatewayConfig,
) -> TaskRejection | None:
    """按 spec「派发的顺序」的表给出第一项不通过的原因；全部通过时为 None。

    - records：本案件已有的任务，包括这一次里前面处理过的
    - batch_round：这一次如果有任务派发出去，是 PM 的第几轮
    """
    if scope.symbol is not None and request.symbol != scope.symbol:
        return TaskRejection.NOT_CASE_STOCK
    if (
        scope.case_type is CaseType.EXPLORATION
        and request.task_type.agent not in _EXPLORATION_AGENTS
    ):
        return TaskRejection.EXPLORATION_TARGET
    used = tasks_used(records, dispatcher)
    if dispatcher is Dispatcher.CRITIC:
        return TaskRejection.CRITIC_TASKS_USED_UP if used >= config.max_critic_tasks else None
    if batch_round > config.max_pm_rounds:
        return TaskRejection.ROUNDS_USED_UP
    if used >= config.max_pm_tasks:
        return TaskRejection.PM_TASKS_USED_UP
    return None


def new_task(
    task_id: UUID,
    scope: CaseScope,
    dispatcher: Dispatcher,
    request: TaskRequest,
    now: datetime,
    pm_round: int | None,
    config: GatewayConfig,
) -> Task:
    """网关为一个不复用的请求生成的 Task：照抄请求，派发时刻是 now，截止时间是 now 加任务时限。

    pm_round 是 PM 派发出去的任务所属的轮次；Critic 的任务和被拒绝的任务为 None。
    """
    return Task(
        task_id=task_id,
        case_id=scope.case_id,
        dispatcher=dispatcher,
        task_type=request.task_type,
        symbol=request.symbol,
        question=request.question,
        inputs=request.inputs,
        dispatched_at=now,
        deadline=now + config.task_timeout,
        pm_round=pm_round,
    )


def rounds_used(records: Iterable[TaskRecord]) -> int:
    """PM 用掉的轮次：派发出去的任务里最大的轮次，没有时为 0。"""
    return max((r.task.pm_round for r in records if r.task.pm_round is not None), default=0)


def tasks_used(records: Iterable[TaskRecord], dispatcher: Dispatcher) -> int:
    """这个发起者派发出去的任务数，不论 Result 的状态；被拒绝的不算。"""
    return sum(1 for r in records if r.task.dispatcher is dispatcher and r.dispatched)


def usage(records: Sequence[TaskRecord]) -> TaskUsage:
    """PM 用掉的轮次和任务数、Critic 用掉的补充研究额度。"""
    return TaskUsage(
        rounds_used(records),
        tasks_used(records, Dispatcher.PM),
        tasks_used(records, Dispatcher.CRITIC),
    )


def dispatched_agents(records: Iterable[TaskRecord]) -> frozenset[SubAgent]:
    """派过任务的子 agent：派发出去的任务的目标，不论 Result 的状态；被拒绝的不算。

    就是调度器结论校验的 dispatched 参数。
    """
    return frozenset(r.task.target for r in records if r.dispatched)


def completed_results(records: Iterable[TaskRecord]) -> dict[str, SubAgent]:
    """状态是完成的 Result 出自哪个子 agent，键是 Result 的 ID（task_id 的文字）。

    就是调度器结论校验的 completed 参数。
    """
    return {
        str(r.task.task_id): r.task.target
        for r in records
        if r.result is not None and r.result.status is ResultStatus.COMPLETED
    }
