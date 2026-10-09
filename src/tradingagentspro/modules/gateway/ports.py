"""Agent 网关需要的外部接口。"""

from typing import Protocol
from uuid import UUID

from tradingagentspro.contracts.task import Result, Task
from tradingagentspro.modules.gateway.domain.dispatch import TaskRecord


class TaskStore(Protocol):
    """任务和 Result 的存储。只追加：Task 生成时写入，Result 出来时写入，写入以后都不再改。

    网关是 Task 和 Result 唯一的写入者；数据库实现（M4）写进状态库，见 state-store spec。
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

    def add_result(self, result: Result) -> None:
        """写入一个 Task 的 Result；service 保证每个 Task 只写一次。"""
        ...
