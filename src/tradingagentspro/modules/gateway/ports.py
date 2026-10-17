"""Agent 网关需要的外部接口。"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from tradingagentspro.contracts.task import Result, Task
from tradingagentspro.modules.gateway.domain.budget import CallCost
from tradingagentspro.modules.gateway.domain.dispatch import TaskRecord


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

    def add(self, entry: CallCost) -> None:
        """写入一条记录。"""
        ...
