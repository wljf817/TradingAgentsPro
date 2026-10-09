"""任务派发的用例：放行或拒绝 PM 和 Critic 的任务请求，把子 agent 的输出、调用失败和超时记成
Result，给出本案件的任务。

见 task-dispatch spec。规则都在 domain 里；这里只读当前时刻、生成 ID、解析输出、读写存储。
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from tradingagentspro.contracts.signal import SubAgentOutput
from tradingagentspro.contracts.task import Dispatcher, Result, Task, TaskRequest
from tradingagentspro.modules.gateway.config import GatewayConfig
from tradingagentspro.modules.gateway.domain.dispatch import (
    CaseScope,
    Dispatch,
    DispatchAction,
    TaskRecord,
    dispatch_rejection,
    new_task,
    reusable,
    rounds_used,
)
from tradingagentspro.modules.gateway.domain.result import (
    Malformed,
    call_failed,
    expired,
    received,
    rejected,
)
from tradingagentspro.modules.gateway.ports import TaskStore
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator

_OUTPUT: Final[TypeAdapter[SubAgentOutput]] = TypeAdapter(SubAgentOutput)


class TaskDispatchService:
    """任务派发的入口。

    同一个案件由调用方串行调用：派发的检查和 Result 只写一次都是先读后写，做法 M4 定。
    """

    def __init__(
        self, store: TaskStore, clock: Clock, ids: IdGenerator, config: GatewayConfig
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._config = config

    def dispatch(
        self, scope: CaseScope, dispatcher: Dispatcher, requests: Sequence[TaskRequest]
    ) -> tuple[Dispatch, ...]:
        """发起者在案件 scope 里调用一次 dispatch_tasks，按请求的顺序逐个处理。

        返回和请求一一对应的处理结果：派发的由调用方交给目标子 agent；被拒绝的 Result 已经写入；
        复用的给出被复用的 Task。三种都用 record(task_id) 取 Result。
        """
        now = self._clock.now()
        records = list(self._store.case_records(scope.case_id))
        # 这一次如果有任务派发出去是第几轮：在开始时算一次，派发以后 rounds_used 就变了
        batch_round = rounds_used(records) + 1
        dispatches = []
        for request in requests:
            reused = reusable(request, records)
            if reused is not None:
                dispatches.append(Dispatch(reused, DispatchAction.REUSED))
                continue
            rejection = dispatch_rejection(
                request, dispatcher, scope, records, batch_round, self._config
            )
            pm_round = batch_round if dispatcher is Dispatcher.PM and rejection is None else None
            task = new_task(
                self._ids.new_id(), scope, dispatcher, request, now, pm_round, self._config
            )
            self._store.add_task(task)
            if rejection is None:
                records.append(TaskRecord(task))
                dispatches.append(Dispatch(task, DispatchAction.DISPATCHED))
            else:
                result = rejected(task, rejection)
                self._store.add_result(result)
                records.append(TaskRecord(task, result))
                dispatches.append(Dispatch(task, DispatchAction.REJECTED))
        return tuple(dispatches)

    def receive(self, task_id: UUID, output: str, cost: Decimal) -> Result:
        """收到子 agent 的输出（JSON 文本）和这个任务的费用；"现在"是收到的时刻。

        已经有 Result 时返回它，不改变。
        """
        return self._settle(
            task_id, lambda task, now: received(task, _parse(output), now, cost, self._config)
        )

    def fail(self, task_id: UUID, detail: str, cost: Decimal) -> Result:
        """调用子 agent 失败、不再重试；detail 是说明。已经有 Result 时返回它，不改变。"""
        return self._settle(task_id, lambda task, now: call_failed(task, detail, now, cost))

    def expire(self, task_id: UUID, cost: Decimal) -> Result:
        """到截止时间仍没有 Result，记超时；早于截止时间抛 ValueError，不写入。

        已经有 Result 时返回它，不改变。
        """
        return self._settle(task_id, lambda task, now: expired(task, now, cost))

    def record(self, task_id: UUID) -> TaskRecord:
        """一个任务和它的 Result；没有这个任务时抛 KeyError。"""
        return self._store.record(task_id)

    def records(self, case_id: UUID) -> tuple[TaskRecord, ...]:
        """本案件的任务和它们的 Result，按生成的先后。"""
        return self._store.case_records(case_id)

    def _settle(self, task_id: UUID, settle: Callable[[Task, datetime], Result]) -> Result:
        """一个任务只有一个 Result：先到的为准。"""
        record = self._store.record(task_id)
        if record.result is not None:
            return record.result
        result = settle(record.task, self._clock.now())
        self._store.add_result(result)
        return result


def _parse(output: str) -> SubAgentOutput | Malformed:
    """把 JSON 文本解析成信号或无信号；解析不了时说明每条错误的位置和原因。"""
    try:
        return _OUTPUT.validate_json(output)
    except ValidationError as error:
        return Malformed("; ".join(f"{_location(e['loc'])}: {e['msg']}" for e in error.errors()))


def _location(loc: tuple[int | str, ...]) -> str:
    """错误的位置，比如 Signal.risks；不是 JSON 时位置为空，写"输出"。"""
    return ".".join(str(part) for part in loc) or "输出"
