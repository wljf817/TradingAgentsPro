"""Agent 网关的表：任务表 gateway_tasks、Result 表 gateway_results、费用表 gateway_costs，以及
TaskStore、CostStore 的数据库实现。

三张表都只追加、一行一条，写入以后不改：Task 生成时一行，一个 Task 最多一行 Result（主键是
task_id），每次尝试一行费用。见 task-dispatch spec「任务的存储」、call-budget spec「费用的存储」和
update-task-cost-storage 的 design D1。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Engine,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    Select,
    String,
    Table,
    Uuid,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.contracts.task import Result, Task
from tradingagentspro.modules.gateway.domain.budget import Budget, CallCost
from tradingagentspro.modules.gateway.domain.dispatch import TaskRecord

# seq 是写入的先后：一次派发的 Task 派发时刻相同，本案件的任务按它排序
TASKS = Table(
    "gateway_tasks",
    MetaData(),
    Column("task_id", Uuid, primary_key=True),
    Column("seq", BigInteger, Identity(always=True), nullable=False),
    Column("case_id", Uuid, nullable=False),
    Column("data", JSONB, nullable=False),
    Index("gateway_tasks_case_id", "case_id"),
)

RESULTS = Table(
    "gateway_results",
    MetaData(),
    Column("task_id", Uuid, primary_key=True),
    Column("data", JSONB, nullable=False),
)

COSTS = Table(
    "gateway_costs",
    MetaData(),
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("case_id", Uuid, nullable=False),
    Column("case_type", String, nullable=False),
    Column("agent", String, nullable=False),
    Column("model_vendor", String, nullable=False),
    Column("model_name", String, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cost", Numeric, nullable=False),
    Column("budget", String, nullable=False),
    Index("gateway_costs_recorded_at", "recorded_at"),
)


class PostgresTaskStore:
    """满足 TaskStore：Task 和 Result 存在状态库所在数据库的 gateway_tasks、gateway_results 表里，
    整份存成 JSON。每个操作一个事务；数据库的错误、读不出来的记录原样抛出。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def case_records(self, case_id: UUID) -> tuple[TaskRecord, ...]:
        query = _with_results().where(TASKS.c.case_id == case_id).order_by(TASKS.c.seq)
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
        return tuple(_record(row) for row in rows)

    def record(self, task_id: UUID) -> TaskRecord:
        query = _with_results().where(TASKS.c.task_id == task_id)
        with self._engine.connect() as connection:
            row = connection.execute(query).first()
        if row is None:
            raise KeyError(task_id)
        return _record(row)

    def add_task(self, task: Task) -> None:
        values = {"task_id": task.task_id, "case_id": task.case_id, "data": _json(task)}
        with self._engine.begin() as connection:
            connection.execute(insert(TASKS).values(values))

    def add_result(self, result: Result) -> Result | None:
        """ON CONFLICT DO NOTHING：另一个事务正在写同一个 task_id 时等它结束；没有写入时在同一个
        事务里读出已有的那行（READ COMMITTED 下这条语句看得到它）。见 design D3。
        """
        write = (
            pg_insert(RESULTS)
            .values(task_id=result.task_id, data=_json(result))
            .on_conflict_do_nothing(index_elements=[RESULTS.c.task_id])
            .returning(RESULTS.c.task_id)
        )
        existing = select(RESULTS.c.data).where(RESULTS.c.task_id == result.task_id)
        with self._engine.begin() as connection:
            if connection.execute(write).first() is not None:
                return None
            return Result.model_validate(connection.execute(existing).scalar_one())


class PostgresCostStore:
    """满足 CostStore：费用记录逐列存在状态库所在数据库的 gateway_costs 表里，一条一个事务；数据库的
    错误原样抛出。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def since(self, moment: datetime) -> tuple[CallCost, ...]:
        query = select(COSTS).where(COSTS.c.recorded_at >= moment).order_by(COSTS.c.id)
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
        return tuple(_cost(row) for row in rows)

    def case_costs(self, case_id: UUID) -> tuple[CallCost, ...]:
        query = select(COSTS).where(COSTS.c.case_id == case_id).order_by(COSTS.c.id)
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
        return tuple(_cost(row) for row in rows)

    def add(self, entry: CallCost) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(COSTS).values(
                    recorded_at=entry.recorded_at,
                    case_id=entry.case_id,
                    case_type=entry.case_type.value,
                    agent=entry.agent.value,
                    model_vendor=entry.model.vendor,
                    model_name=entry.model.name,
                    input_tokens=entry.usage.input_tokens,
                    output_tokens=entry.usage.output_tokens,
                    cost=entry.cost,
                    budget=entry.budget.value,
                )
            )


def _with_results() -> Select[tuple[Any, Any]]:
    """每个 Task 连上它的 Result：还没有 Result 时右边为空。"""
    return select(TASKS.c.data.label("task"), RESULTS.c.data.label("result")).select_from(
        TASKS.outerjoin(RESULTS, TASKS.c.task_id == RESULTS.c.task_id)
    )


def _record(row: Any) -> TaskRecord:
    result = None if row.result is None else Result.model_validate(row.result)
    return TaskRecord(Task.model_validate(row.task), result)


def _json(model: Task | Result) -> dict[str, Any]:
    """按 JSON 模式写出：金额、耗时、时刻都是文字，读回不丢精度。"""
    return model.model_dump(mode="json")


def _cost(row: Any) -> CallCost:
    return CallCost(
        case_id=row.case_id,
        case_type=CaseType(row.case_type),
        agent=Agent(row.agent),
        model=ModelRef(vendor=row.model_vendor, name=row.model_name),
        usage=TokenUsage(input_tokens=row.input_tokens, output_tokens=row.output_tokens),
        cost=row.cost,
        budget=Budget(row.budget),
        recorded_at=row.recorded_at,
    )
