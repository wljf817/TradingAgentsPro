"""子 agent 的输出、调用失败、超时和被拒绝变成 Result，见 task-dispatch spec「子 agent 的输出变成
Result」「超时」「调用失败」「Result 的格式」。

截止时间先看：晚于截止时间收到的输出或调用失败，不论合不合法都是超时。格式不合法的两个来源合在
一起：结构错误由调用方解析时给出 Malformed，评估窗口和数据截至时刻由 signal-format 的
signal_rejection 检查；都记为失败、原因是格式不合法，这就是任务最终的 Result，不重新调用子 agent。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from tradingagentspro.contracts.signal import NoSignal, Signal, SubAgentOutput
from tradingagentspro.contracts.task import (
    Result,
    ResultStatus,
    Task,
    TaskFailure,
    TaskRejection,
)
from tradingagentspro.modules.gateway.config import GatewayConfig
from tradingagentspro.modules.gateway.domain.signal import signal_rejection


@dataclass(frozen=True, slots=True)
class Malformed:
    """不能解析成格式正确的信号或无信号的输出。detail 指出是哪一项、为什么。"""

    detail: str


def received(
    task: Task,
    output: SubAgentOutput | Malformed,
    now: datetime,
    cost: Decimal,
    config: GatewayConfig,
) -> Result:
    """收到子 agent 的输出时的 Result。now 是收到输出的时刻，cost 是这个任务的费用。"""
    if now > task.deadline:
        return _settled(task, ResultStatus.TIMED_OUT, now, cost)
    if isinstance(output, Malformed):
        return _failed(task, TaskFailure.FORMAT_INVALID, output.detail, now, cost)
    rejection = signal_rejection(output, now, config)
    if rejection is not None:
        return _failed(task, TaskFailure.FORMAT_INVALID, rejection.value, now, cost)
    status = ResultStatus.COMPLETED if isinstance(output, Signal) else ResultStatus.NO_SIGNAL
    return _settled(task, status, now, cost, output)


def call_failed(task: Task, detail: str, now: datetime, cost: Decimal) -> Result:
    """调用方给出调用失败时的 Result，detail 是调用方的说明；晚于截止时间是超时。"""
    if now > task.deadline:
        return _settled(task, ResultStatus.TIMED_OUT, now, cost)
    return _failed(task, TaskFailure.CALL_FAILED, detail, now, cost)


def expired(task: Task, now: datetime, cost: Decimal) -> Result:
    """到截止时间仍没有 Result：超时。早于截止时间抛 ValueError：调用方只在截止时间到了才调用。"""
    if now < task.deadline:
        raise ValueError(f"任务 {task.task_id} 还没到截止时间")
    return _settled(task, ResultStatus.TIMED_OUT, now, cost)


def rejected(task: Task, reason: TaskRejection) -> Result:
    """被拒绝的 Result：没有调用子 agent，耗时和费用都是 0。"""
    return Result(
        task_id=task.task_id,
        status=ResultStatus.REJECTED,
        reason=reason,
        elapsed=timedelta(0),
        cost=Decimal(0),
    )


def _settled(
    task: Task,
    status: ResultStatus,
    now: datetime,
    cost: Decimal,
    output: Signal | NoSignal | None = None,
) -> Result:
    """完成、无信号或超时的 Result：耗时是 now 减派发时刻。"""
    return Result(
        task_id=task.task_id,
        status=status,
        output=output,
        elapsed=now - task.dispatched_at,
        cost=cost,
    )


def _failed(task: Task, reason: TaskFailure, detail: str, now: datetime, cost: Decimal) -> Result:
    """失败的 Result：耗时是 now 减派发时刻。"""
    return Result(
        task_id=task.task_id,
        status=ResultStatus.FAILED,
        reason=reason,
        detail=detail,
        elapsed=now - task.dispatched_at,
        cost=cost,
    )
