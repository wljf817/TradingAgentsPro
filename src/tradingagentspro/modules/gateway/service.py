"""Agent 网关的用例。

- TaskDispatchService：放行或拒绝 PM 和 Critic 的任务请求，把子 agent 的输出、调用失败和超时记成
  Result，给出本案件的任务，见 task-dispatch spec。这里只读当前时刻、生成 ID、解析输出、读写存储
- ModelFallbackService：一次调用向哪个模型尝试、失败以后换不换，按模型熔断，记下案件内的起点，见
  model-fallback spec。这里只读当前时刻、查模型的配置，在内存里保存熔断状态和起点
- CallBudgetService：记下每次尝试的费用，给出三个预算的情况，见 call-budget spec。这里只读当前时刻、
  查交易日历和单价、读写存储

规则都在 domain 里。
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.model import ModelRef, ModelUse, TokenUsage
from tradingagentspro.contracts.signal import SubAgentOutput
from tradingagentspro.contracts.task import Dispatcher, Result, Task, TaskRequest
from tradingagentspro.modules.gateway.config import GatewayConfig, ModelAssignments
from tradingagentspro.modules.gateway.domain.budget import (
    Budget,
    BudgetStatus,
    CallCost,
    budget_status,
    charged_budget,
    cost_of,
)
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
from tradingagentspro.modules.gateway.domain.fallback import (
    Breaker,
    CallError,
    CallStep,
    ModelCall,
    after_failure,
    exhausted,
    start,
)
from tradingagentspro.modules.gateway.domain.result import (
    Malformed,
    call_failed,
    expired,
    received,
    rejected,
)
from tradingagentspro.modules.gateway.ports import CostStore, TaskStore
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar

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

    def receive(self, task_id: UUID, output: str, cost: Decimal, model_use: ModelUse) -> Result:
        """收到子 agent 的输出（JSON 文本）、这个任务的费用和给出输出的模型；"现在"是收到的时刻。

        已经有 Result 时返回它，不改变。
        """
        return self._settle(
            task_id,
            lambda task, now: received(task, _parse(output), now, cost, model_use, self._config),
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


class ModelFallbackService:
    """备用模型的入口：网关不调用模型，调用方按返回的步骤去尝试，把每次尝试的结果报告回来。

    熔断状态和案件内的起点都放在内存里：新建以后所有模型都正常、所有起点都是主模型，进程重启以后
    从头算（add-model-fallback 的 design D6、D7）。同一个实例由调用方串行调用。
    """

    def __init__(self, clock: Clock, models: ModelAssignments, config: GatewayConfig) -> None:
        self._clock = clock
        self._models = models
        self._config = config
        self._breakers: dict[ModelRef, Breaker] = {}
        # 每个案件、每个 agent 的起点在 chain 里的位置；没有记录时是 0（主模型）
        self._starts: dict[tuple[UUID, Agent], int] = {}

    def begin(self, case_id: UUID, agent: Agent, deadline: datetime | None = None) -> CallStep:
        """为案件里的 agent 开始一次调用，给出第一步。deadline 是子 agent 任务的截止时间。"""
        chain = self._models.of(agent).chain
        first = self._starts.get((case_id, agent), 0)
        return start(
            case_id, agent, chain, first, deadline, self._breakers, self._clock.now(), self._config
        )

    def fail(self, call: ModelCall, error: CallError) -> CallStep:
        """正在进行的尝试失败了，给出下一步。这是这个模型的最后一次尝试时，它不可用，计入熔断。"""
        now = self._clock.now()
        if exhausted(call, self._config):
            self._breakers[call.model] = self._breaker(call.model).failed(now, self._config)
        return after_failure(call, error, self._breakers, now, self._config)

    def succeed(self, call: ModelCall) -> ModelUse:
        """正在进行的尝试拿到了输出（格式不合法也算）：模型回到正常，起点只往后移。"""
        self._breakers[call.model] = self._breaker(call.model).succeeded()
        key = (call.case_id, call.agent)
        self._starts[key] = max(self._starts.get(key, 0), call.index)
        return ModelUse(model=call.model, fallback=call.index > 0)

    def case_model(self, case_id: UUID, agent: Agent) -> ModelUse:
        """本案件这个 agent 的起点，以及它是不是备用模型。"""
        index = self._starts.get((case_id, agent), 0)
        return ModelUse(model=self._models.of(agent).chain[index], fallback=index > 0)

    def end_case(self, case_id: UUID) -> None:
        """案件结束：清掉这个案件所有 agent 的起点。"""
        for key in [key for key in self._starts if key[0] == case_id]:
            del self._starts[key]

    def has_available_model(self, agent: Agent) -> bool:
        """这个 agent 还有可用的模型：主模型或者任何一个备用模型不在冷却中。"""
        now = self._clock.now()
        return any(not self._breaker(model).cooling(now) for model in self._models.of(agent).chain)

    def cooling(self) -> dict[ModelRef, datetime]:
        """现在冷却中的模型和各自冷却结束的时刻。"""
        now = self._clock.now()
        return {
            model: breaker.cooling_until
            for model, breaker in self._breakers.items()
            if breaker.cooling_until is not None and breaker.cooling(now)
        }

    def _breaker(self, model: ModelRef) -> Breaker:
        """这个模型的熔断状态；没有记录时是正常。"""
        return self._breakers.get(model, Breaker())


class CallBudgetService:
    """调用预算的入口：记下每次尝试的费用，给出三个预算的情况，见 call-budget spec。

    这里只读当前时刻、查单价、读写存储；预算的情况每次从存储里今天的记录重新算，不缓存，进程重启
    以后接着算。网关不因为预算用完拒绝任务或调用（spec「已经开始的案件」）。同一个实例由调用方串行
    调用：记到哪个预算是先读后写，做法 M4 定。
    """

    def __init__(
        self,
        store: CostStore,
        clock: Clock,
        calendar: TradingCalendar,
        models: ModelAssignments,
        config: GatewayConfig,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._models = models
        self._config = config

    def record(
        self, scope: CaseScope, agent: Agent, model: ModelRef, usage: TokenUsage
    ) -> CallCost:
        """为案件 scope 里 agent 的一次尝试记下费用，"现在"是记下的时刻；model 没有单价时抛
        KeyError。返回记下的一条，子 agent 任务的费用由调用方把这个任务各条的 cost 加起来。
        """
        now = self._clock.now()
        general_exhausted = Budget.GENERAL in self._status(now).exhausted
        entry = CallCost(
            case_id=scope.case_id,
            case_type=scope.case_type,
            agent=agent,
            model=model,
            usage=usage,
            cost=cost_of(usage, self._models.price(model)),
            budget=charged_budget(scope.case_type, general_exhausted),
            recorded_at=now,
        )
        self._store.add(entry)
        return entry

    def status(self) -> BudgetStatus:
        """现在归属的交易日里三个预算的情况。"""
        return self._status(self._clock.now())

    def _status(self, now: datetime) -> BudgetStatus:
        """now 归属的交易日从上一个交易日的收盘时刻开始。"""
        start = self._calendar.last_session_before(now).close
        day = self._calendar.session_closing_after(now).day
        return budget_status(self._store.since(start), day, self._config)


def _parse(output: str) -> SubAgentOutput | Malformed:
    """把 JSON 文本解析成信号或无信号；解析不了时说明每条错误的位置和原因。"""
    try:
        return _OUTPUT.validate_json(output)
    except ValidationError as error:
        return Malformed("; ".join(f"{_location(e['loc'])}: {e['msg']}" for e in error.errors()))


def _location(loc: tuple[int | str, ...]) -> str:
    """错误的位置，比如 Signal.risks；不是 JSON 时位置为空，写"输出"。"""
    return ".".join(str(part) for part in loc) or "输出"
