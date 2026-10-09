"""测试用的任务存储：存在内存里，Task 按写入的先后，Result 按 task_id。"""

from uuid import UUID

from tradingagentspro.contracts.task import Result, Task
from tradingagentspro.modules.gateway import TaskRecord


class InMemoryTaskStore:
    """满足 Agent 网关的 TaskStore。只追加：同一个 Task 写两次、同一个 Task 写第二个 Result、
    给不存在的 Task 写 Result 都抛错，让测试发现用例的问题。
    """

    def __init__(self) -> None:
        self._tasks: dict[UUID, Task] = {}  # 按写入的先后
        self._results: dict[UUID, Result] = {}

    def case_records(self, case_id: UUID) -> tuple[TaskRecord, ...]:
        return tuple(self.record(t.task_id) for t in self._tasks.values() if t.case_id == case_id)

    def record(self, task_id: UUID) -> TaskRecord:
        return TaskRecord(self._tasks[task_id], self._results.get(task_id))

    def add_task(self, task: Task) -> None:
        if task.task_id in self._tasks:
            raise ValueError(f"Task {task.task_id} 已经写入过")
        self._tasks[task.task_id] = task

    def add_result(self, result: Result) -> None:
        if result.task_id not in self._tasks:
            raise KeyError(result.task_id)
        if result.task_id in self._results:
            raise ValueError(f"Task {result.task_id} 已经有 Result")
        self._results[result.task_id] = result
