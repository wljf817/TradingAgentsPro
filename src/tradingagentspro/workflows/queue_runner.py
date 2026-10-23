"""排队运行：触发源排进来的案件持久地排队；每一轮组出队列的快照、取得队列的决定，按作废、不再运行、
启动的顺序执行；给出调度器心跳，写调用预算用完的程序日志。

见 queue-runner spec 和 add-queue-runner 的 design D1 到 D4、D8 到 D10、D13。排队的案件存在调度器
的表里（QueueService），运行中的案件就是 DBOS 里 PENDING 的案件工作流（running_cases）；规则全在
调度器的纯函数 queue_decision 里，这里只组快照和执行决定。排队运行在自己的守护线程里每隔一个间隔做
一轮，不靠通知：快照的每一项都在下一轮里重新读（design D3）。

排队运行不调用模型、不做交易判断、不下单、不拿全程序共用的那把锁：开始运行以后的事由案件工作流做，
作废交给方案的下单流程（order_workflow.void_plan，design D6）。排队、不再运行、方案作废写调度器的
程序日志，调用预算用完写系统的程序日志。
"""

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from dbos import DBOS
from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.caserecords import CaseRecordService, CaseTrigger
from tradingagentspro.modules.gateway import Budget, BudgetStatus, CallBudgetService, GatewayConfig
from tradingagentspro.modules.health import HealthService
from tradingagentspro.modules.programlog import WHOLE_SYSTEM, Action, Actor, ProgramLog
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    QueuedCase,
    QueueExit,
    QueueService,
    QueueSnapshot,
    SchedulerConfig,
    Trigger,
    queue_decision,
)
from tradingagentspro.modules.stockpool import StockPoolService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.workflows.case_entries import case_trigger
from tradingagentspro.workflows.case_workflow import (
    CaseProgress,
    queue_view,
    running_cases,
    start_case,
)
from tradingagentspro.workflows.names import ACTIONS, STOCK_STATUSES
from tradingagentspro.workflows.order_flow import VoidCause
from tradingagentspro.workflows.order_workflow import void_plan

_log = logging.getLogger(__name__)

# 预算的中文名和用完以后的影响，见 queue-runner spec「调用预算用完的日志」
_BUDGETS: Final = {
    Budget.GENERAL: (
        "普通预算",
        "关注股、候选和复盘的案件留在队列里，下一个交易日恢复以后运行；持仓的案件改用持仓保留预算",
    ),
    Budget.RESERVE: ("持仓保留预算", "健康监控进入降级模式，不开始新案件"),
    Budget.EXPLORATION: ("寻股预算", "寻股留在队列里，下一个交易日恢复以后运行"),
}


class QueueRunnerConfig(BaseModel):
    """排队运行的配置和默认值，含义见 queue-runner spec「排队运行的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 一轮做完以后等这么久再做下一轮
    interval: timedelta = Field(default=timedelta(seconds=5), gt=timedelta(0))


class CaseControl(Protocol):
    """排队运行对案件工作流和下单流程的那几下；生产是 DbosCases，测试换成内存的替身。"""

    def running(self) -> tuple[tuple[UUID, CaseProgress], ...]:
        """运行中的案件和它们的进展。"""
        ...

    def start(self, case: QueuedCase, definition: CaseDefinition) -> None:
        """以这一轮确定的案件类型开始运行这个排队的案件。"""
        ...

    def exists(self, case_id: UUID) -> bool:
        """这个案件的工作流已经建立（开始运行过）。"""
        ...

    def void(self, plan_id: UUID) -> None:
        """把作废交给这个方案的下单流程。"""
        ...


class DbosCases:
    """满足 CaseControl：案件工作流和下单流程的 DBOS 实现，见 design D4、D5、D6。"""

    def running(self) -> tuple[tuple[UUID, CaseProgress], ...]:
        return running_cases()

    def start(self, case: QueuedCase, definition: CaseDefinition) -> None:
        start_case(case.case_id, definition, case.trigger, case.triggered_at)

    def exists(self, case_id: UUID) -> bool:
        return DBOS.get_workflow_status(str(case_id)) is not None

    def void(self, plan_id: UUID) -> None:
        void_plan(plan_id, VoidCause.NEW_CASE)


@dataclass(frozen=True, slots=True)
class QueueParts:
    """排队运行用到的部件。exiting 给出正在紧急退出的股票；gateway 给出预算的额度，写日志用。"""

    queue: QueueService
    cases: CaseControl
    pool: StockPoolService
    health: HealthService
    budget: CallBudgetService
    gateway: GatewayConfig
    records: CaseRecordService
    exiting: Callable[[], frozenset[str]]
    scheduler: SchedulerConfig
    log: ProgramLog
    clock: Clock


class QueueRunner:
    """排队运行，见 queue-runner spec。

    enqueue 可以从任何线程调用，只写表和程序日志；run_round 做一轮，可以直接调用；start 先让开始运行
    了、还没出队的案件出队，再开守护线程循环；stop 停下并等线程结束；heartbeat 给出调度器心跳。
    """

    def __init__(self, parts: QueueParts, config: QueueRunnerConfig) -> None:
        self._parts = parts
        self._config = config
        # 调度器最近一次心跳：装配好的时刻，之后是每一轮做完的时刻（design D8）
        self._beat = parts.clock.now()
        self._voided: set[UUID] = set()  # 这次运行里作废过的方案
        self._reported: set[tuple[Budget, date]] = set()  # 这次运行里写过用完的预算和交易日
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue(self, trigger: Trigger, triggered_at: datetime) -> UUID:
        """排进一个案件，交回新的案件 ID；存下以后写程序日志的排队。见 spec「排队」。"""
        parts = self._parts
        case = parts.queue.enqueue(trigger, triggered_at)
        recorded = case_trigger(trigger, triggered_at)
        detail = f"{recorded.reason}；触发时刻 {_utc(triggered_at)}"
        parts.log.write(
            Actor.SCHEDULER, Action.ENQUEUE, str(case.case_id), detail, case_id=case.case_id
        )
        return case.case_id

    def heartbeat(self) -> datetime:
        """调度器最近一次心跳的时刻，见 spec「调度器心跳」。"""
        return self._beat

    def start(self) -> None:
        """开始排队运行：先让重启以前开始运行了、还没出队的案件按启动出队（spec「重启以后」），再开
        守护线程 case-queue，立即做第一轮。写一条 INFO：排队运行已开始和间隔。
        """
        parts = self._parts
        for case in parts.queue.queued():
            if parts.cases.exists(case.case_id):
                parts.queue.leave(case.case_id, QueueExit.STARTED)
        seconds = self._config.interval.total_seconds()
        self._thread = threading.Thread(target=self._loop, name="case-queue", daemon=True)
        self._thread.start()
        _log.info("排队运行已开始，间隔 %g 秒", seconds)

    def stop(self) -> None:
        """不再开始新的一轮，等正在做的一轮做完、线程结束；没有开始过时什么都不做。"""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        """做一轮，等一个间隔，直到停止；一轮出错写 ERROR，下一轮照常。"""
        seconds = self._config.interval.total_seconds()
        while not self._stopping.is_set():
            try:
                self.run_round()
            except Exception:
                _log.exception("排队运行：这一轮出错")
            self._stopping.wait(seconds)

    def run_round(self) -> None:
        """做一轮，见 spec「一轮」：组出快照、取得队列的决定，先作废、再不再运行、最后启动；做完
        记下心跳。出错时原样抛出，这一轮剩下的不做，心跳不变。
        """
        parts = self._parts
        running = parts.cases.running()
        budget = parts.budget.status()
        self._report_budget(budget)
        statuses = {entry.symbol: entry.status for entry in parts.pool.entries()}
        queued = parts.queue.queued()
        in_progress, pending = queue_view(running)
        snapshot = QueueSnapshot(
            queued=queued,
            running=in_progress,
            plans=pending,
            statuses=statuses,
            exiting=parts.exiting(),
            degraded=parts.health.status.degraded,
            general_budget_exhausted=Budget.GENERAL in budget.exhausted,
            exploration_budget_exhausted=Budget.EXPLORATION in budget.exhausted,
        )
        decision = queue_decision(snapshot, parts.scheduler)
        plans = {p.plan.plan_id: (case_id, p.plan) for case_id, p in running if p.plan is not None}
        for plan_id in decision.void:
            if plan_id not in self._voided:
                self._void(*plans[plan_id])
        cases = {case.case_id: case for case in queued}
        for case_id in decision.drop:
            self._drop(cases[case_id], statuses)
        for start in decision.start:
            parts.cases.start(cases[start.case_id], start.definition)
            parts.queue.leave(start.case_id, QueueExit.STARTED)
        self._beat = parts.clock.now()

    def _void(self, case_id: UUID, plan: TradePlan) -> None:
        """写程序日志的方案作废，交给下单流程作废，见 spec「作废方案」。"""
        parts = self._parts
        detail = (
            f"{ACTIONS[plan.action]} {plan.symbol}：{plan.symbol} 有新案件排队，"
            "作废已交人工确认、还没下单的开仓方案"
        )
        parts.log.write(
            Actor.SCHEDULER, Action.VOID_PLAN, str(plan.plan_id), detail, case_id=case_id
        )
        parts.cases.void(plan.plan_id)
        self._voided.add(plan.plan_id)

    def _drop(self, case: QueuedCase, statuses: Mapping[str, StockStatus]) -> None:
        """写案件记录的不再运行、程序日志的不再运行，按不再运行出队，见 spec「不再运行」。"""
        parts = self._parts
        recorded = case_trigger(case.trigger, case.triggered_at)
        parts.records.not_run(case.case_id, recorded)
        parts.log.write(
            Actor.SCHEDULER,
            Action.DROP_CASE,
            str(case.case_id),
            _drop_text(recorded, statuses),
            case_id=case.case_id,
        )
        parts.queue.leave(case.case_id, QueueExit.DROPPED)

    def _report_budget(self, budget: BudgetStatus) -> None:
        """这次运行里每个交易日、每个预算第一次看到用完时写一条，见 spec「调用预算用完的日志」。"""
        limits = {
            Budget.GENERAL: self._parts.gateway.general_budget,
            Budget.RESERVE: self._parts.gateway.reserve_budget,
            Budget.EXPLORATION: self._parts.gateway.exploration_budget,
        }
        for kind in Budget:
            if kind not in budget.exhausted or (kind, budget.day) in self._reported:
                continue
            name, effect = _BUDGETS[kind]
            detail = (
                f"{name}用完：交易日 {budget.day.isoformat()}，今天的费用 {budget.spent[kind]}，"
                f"额度 {limits[kind]}。{effect}"
            )
            self._parts.log.write(Actor.SYSTEM, Action.BUDGET_EXHAUSTED, WHOLE_SYSTEM, detail)
            self._reported.add((kind, budget.day))


def _drop_text(recorded: CaseTrigger, statuses: Mapping[str, StockStatus]) -> str:
    """不再运行的说明：触发原因和这只股票现在的状态。不再运行的都是针对单只股票的案件。"""
    symbol = recorded.symbol
    assert symbol is not None  # 寻股和复盘总能确定类型，不会不再运行
    status = statuses.get(symbol)
    now = f"{symbol} 不在股票池里" if status is None else f"{symbol} 现在是{STOCK_STATUSES[status]}"
    return f"{recorded.reason}；{now}"


def _utc(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
