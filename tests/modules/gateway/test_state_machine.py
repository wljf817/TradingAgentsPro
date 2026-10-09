"""任务派发的状态机性质测试（add-task-dispatch 的 design D12）。

一个单只股票的案件、一个寻股：随机交错 PM 和 Critic 的派发（任务类型、股票、问题、输入从小的集合
里取，也常常照抄已有的任务，让重复和超限经常出现）、给任务交输出（合法的信号、无信号、结构错误、
评估窗口不合法、数据截至时刻晚于现在）、调用失败、拨动时间和记超时。每次派发按参考模型推出每个请求
的处理结果；每一步之后检查：PM 派发出去的任务不超过上限，轮次是从 1 开始连续的数、不超过上限，
Critic 派发出去的不超过额度；寻股没有派给 News 的任务，单只股票的案件派发出去的都是案件的股票；
同一个案件里可以复用的相同任务最多一个；被拒绝的任务一生成就有 Result，没有轮次；每个任务最多一个
Result，写入以后不变，晚于截止时间生成的 Result 都是超时。配置用较小的上限。
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIds
from tests.fakes.tasks import InMemoryTaskStore
from tests.modules.gateway.builders import NO_SIGNAL_JSON, NOW, SIGNAL_JSON, signal
from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.task import (
    Dispatcher,
    Result,
    ResultStatus,
    Task,
    TaskFailure,
    TaskRejection,
    TaskRequest,
    TaskType,
)
from tradingagentspro.modules.gateway import (
    CaseScope,
    DispatchAction,
    GatewayConfig,
    TaskDispatchService,
    TaskRecord,
    completed_results,
    dispatched_agents,
    usage,
)

PM, CRITIC = Dispatcher.PM, Dispatcher.CRITIC
COMPLETED, NO_SIGNAL, TIMED_OUT, FAILED, REJECTED = (
    ResultStatus.COMPLETED,
    ResultStatus.NO_SIGNAL,
    ResultStatus.TIMED_OUT,
    ResultStatus.FAILED,
    ResultStatus.REJECTED,
)
CONFIG = GatewayConfig(max_pm_tasks=4, max_pm_rounds=2, max_critic_tasks=1)
SINGLE = CaseScope(UUID(int=0xC001), CaseType.WATCH_EVENT, "BBB")
EXPLORATION = CaseScope(UUID(int=0xC002), CaseType.EXPLORATION, None)
CASES = (SINGLE, EXPLORATION)

# 请求的各项：两种派给 News，两种派给 Quant，一种派给 Fundamentals；股票大多是单只股票的案件的
# BBB，让派发出去的任务常常多到碰上任务数上限
TYPES = (
    TaskType.EVENT_IMPACT,
    TaskType.NEWS_SINCE,
    TaskType.PRICE_REACTION,
    TaskType.VOLATILITY,
    TaskType.VALUATION,
)
SYMBOLS = ("BBB", "BBB", "BBB", "DDD")
QUESTIONS = ("问题 1", "问题 2")
INPUTS: tuple[tuple[str, ...], ...] = ((), ("E-1",), ("E-1", "E-2"))
_FRESH = st.builds(
    TaskRequest,
    task_type=st.sampled_from(TYPES),
    symbol=st.sampled_from(SYMBOLS),
    question=st.sampled_from(QUESTIONS),
    inputs=st.sampled_from(INPUTS),
)
DISPATCHERS = (PM, PM, CRITIC)
_REUSABLE = {COMPLETED, NO_SIGNAL}

_without_risks = json.loads(SIGNAL_JSON)
del _without_risks["risks"]
# 交给网关的输出：种类、期望的状态（不晚于截止时间时）
OUTPUTS = ("信号", "无信号", "结构错误", "评估窗口 4", "数据截至晚于现在")
_EXPECTED_STATUS = {
    "信号": COMPLETED,
    "无信号": NO_SIGNAL,
    "结构错误": FAILED,
    "评估窗口 4": FAILED,
    "数据截至晚于现在": FAILED,
}

_REJECTION_EVENTS = {
    TaskRejection.NOT_CASE_STOCK: "拒绝：股票不是案件的股票",
    TaskRejection.EXPLORATION_TARGET: "拒绝：寻股只能派给 Quant 和 Fundamentals",
    TaskRejection.ROUNDS_USED_UP: "拒绝：轮次已用完",
    TaskRejection.PM_TASKS_USED_UP: "拒绝：任务数已用完",
    TaskRejection.CRITIC_TASKS_USED_UP: "拒绝：补充研究额度已用完",
}


def _same(request: TaskRequest, task: Task) -> bool:
    """spec「相同的任务」：任务类型、股票、问题相同，输入的条目不论顺序相同。"""
    return (request.task_type, request.symbol, request.question, set(request.inputs)) == (
        task.task_type,
        task.symbol,
        task.question,
        set(task.inputs),
    )


def _rejected(record: TaskRecord) -> bool:
    return record.result is not None and record.result.status is REJECTED


class TaskDispatchMachine(RuleBasedStateMachine):
    """参考模型自己记下发出过的 task_id 和第一次看到的 Result，检查时只读存储里的记录，不读派发规则
    的实现。
    """

    def __init__(self) -> None:
        super().__init__()
        self.clock = FakeClock(NOW)
        self.store = InMemoryTaskStore()
        self.service = TaskDispatchService(self.store, self.clock, FakeIds(), CONFIG)
        self.task_ids: list[UUID] = []  # 按生成的先后
        self.results: dict[UUID, Result] = {}  # 第一次看到的 Result

    @property
    def now(self) -> datetime:
        return self.clock.now()

    def _records(self, case: CaseScope) -> tuple[TaskRecord, ...]:
        return self.store.case_records(case.case_id)

    def _all(self) -> list[TaskRecord]:
        return [self.store.record(task_id) for task_id in self.task_ids]

    # 派发

    @rule(
        data=st.data(),
        case=st.sampled_from(CASES),
        dispatcher=st.sampled_from(DISPATCHERS),
        count=st.integers(0, 6),
    )
    def dispatch(
        self, data: st.DataObject, case: CaseScope, dispatcher: Dispatcher, count: int
    ) -> None:
        """一次派发 count 个请求。每个请求有时照抄本案件已有的任务（输入可能倒过来），其余随机。
        一次最多 6 个请求，PM 派得比 Critic 多，让一次里超出任务数上限的情况也常常出现。
        """
        requests = [self._draw_request(data, case) for _ in range(count)]
        known = list(self._records(case))
        batch_round = max((r.task.pm_round or 0 for r in known), default=0) + 1

        dispatches = self.service.dispatch(case, dispatcher, requests)

        assert len(dispatches) == len(requests)
        for request, dispatch in zip(requests, dispatches, strict=True):
            expected = self._expected(request, dispatcher, case, known, batch_round)
            if isinstance(expected, Task):
                assert dispatch.action is DispatchAction.REUSED
                assert dispatch.task == expected
                event("复用")
                continue
            task = dispatch.task
            assert task.task_id not in self.task_ids, "不复用的请求生成新 Task"
            self.task_ids.append(task.task_id)
            assert (task.case_id, task.dispatcher, task.dispatched_at) == (
                case.case_id,
                dispatcher,
                self.now,
            )
            assert task.deadline == self.now + CONFIG.task_timeout
            assert (task.task_type, task.symbol, task.question, task.inputs) == (
                request.task_type,
                request.symbol,
                request.question,
                request.inputs,
            )
            result = self.store.record(task.task_id).result
            if expected is None:
                assert dispatch.action is DispatchAction.DISPATCHED
                assert result is None
                assert task.pm_round == (batch_round if dispatcher is PM else None)
                event("派发")
            else:
                assert dispatch.action is DispatchAction.REJECTED
                assert result is not None, "被拒绝的任务一生成就有 Result"
                assert (result.status, result.reason) == (REJECTED, expected)
                assert task.pm_round is None
                event(_REJECTION_EVENTS[expected])
            known.append(TaskRecord(task, result))
        if (
            dispatcher is PM
            and dispatches
            and not any(d.action is DispatchAction.DISPATCHED for d in dispatches)
        ):
            event("PM 这一次没有派发出去，不算一轮")

    def _draw_request(self, data: st.DataObject, case: CaseScope) -> TaskRequest:
        existing = [r.task for r in self._records(case)]
        if not existing or not data.draw(st.booleans()):
            return data.draw(_FRESH)
        task = data.draw(st.sampled_from(existing))
        inputs = task.inputs[::-1] if data.draw(st.booleans()) else task.inputs
        return TaskRequest(
            task_type=task.task_type, symbol=task.symbol, question=task.question, inputs=inputs
        )

    def _expected(
        self,
        request: TaskRequest,
        dispatcher: Dispatcher,
        case: CaseScope,
        known: list[TaskRecord],
        batch_round: int,
    ) -> Task | TaskRejection | None:
        """按 spec「派发的顺序」从参考模型推出的结果：复用的 Task，被拒绝的原因，或者派发时为
        None。
        """
        for record in known:
            if _same(request, record.task) and (
                record.result is None or record.result.status in _REUSABLE
            ):
                return record.task
        used = sum(1 for r in known if r.task.dispatcher is dispatcher and not _rejected(r))
        checks = [
            (
                TaskRejection.NOT_CASE_STOCK,
                case.symbol is not None and request.symbol != case.symbol,
            ),
            (
                TaskRejection.EXPLORATION_TARGET,
                case.case_type is CaseType.EXPLORATION and request.task_type.agent is SubAgent.NEWS,
            ),
            (TaskRejection.ROUNDS_USED_UP, dispatcher is PM and batch_round > CONFIG.max_pm_rounds),
            (TaskRejection.PM_TASKS_USED_UP, dispatcher is PM and used >= CONFIG.max_pm_tasks),
            (
                TaskRejection.CRITIC_TASKS_USED_UP,
                dispatcher is CRITIC and used >= CONFIG.max_critic_tasks,
            ),
        ]
        return next((rejection for rejection, fails in checks if fails), None)

    # 输出、调用失败和超时

    def _pick(self, data: st.DataObject) -> TaskRecord | None:
        """大多取还在运行的任务，有时任取一个（包括已经有 Result 的）；没有任务时为 None。"""
        records = self._all()
        running = [r for r in records if r.result is None]
        if not records:
            return None
        pool = running if running and data.draw(st.integers(0, 3)) > 0 else records
        return data.draw(st.sampled_from(pool))

    def _settled_already(self, record: TaskRecord, result: Result) -> bool:
        """已经有 Result 的任务：给出已有的 Result，不变。"""
        if record.result is None:
            return False
        assert result == record.result
        assert self.store.record(record.task.task_id).result == record.result
        event("已有 Result，不变")
        return True

    @rule(data=st.data(), kind=st.sampled_from(OUTPUTS), cost=st.sampled_from(["0", "0.02"]))
    def receive(self, data: st.DataObject, kind: str, cost: str) -> None:
        record = self._pick(data)
        if record is None:
            return
        output = self._output(kind)

        result = self.service.receive(record.task.task_id, output, Decimal(cost))

        if self._settled_already(record, result):
            return
        if self.now > record.task.deadline:
            assert (result.status, result.output) == (TIMED_OUT, None)
            event("截止时间以后收到输出：超时")
            return
        assert result.status is _EXPECTED_STATUS[kind]
        if result.status is FAILED:
            assert result.reason is TaskFailure.FORMAT_INVALID
        assert result.cost == Decimal(cost)
        event(f"收到{kind}")

    def _output(self, kind: str) -> str:
        match kind:
            case "信号":
                return SIGNAL_JSON
            case "无信号":
                return NO_SIGNAL_JSON
            case "结构错误":
                return json.dumps(_without_risks)
            case "评估窗口 4":
                return signal(horizon=4).model_dump_json()
            case _:
                return signal(data_as_of=self.now + timedelta(seconds=1)).model_dump_json()

    @rule(data=st.data())
    def fail(self, data: st.DataObject) -> None:
        record = self._pick(data)
        if record is None:
            return

        result = self.service.fail(record.task.task_id, "主模型和备用模型都失败", Decimal(0))

        if self._settled_already(record, result):
            return
        if self.now > record.task.deadline:
            assert result.status is TIMED_OUT
            event("截止时间以后调用失败：超时")
            return
        assert (result.status, result.reason, result.detail) == (
            FAILED,
            TaskFailure.CALL_FAILED,
            "主模型和备用模型都失败",
        )
        event("调用失败")

    @rule(data=st.data())
    def expire(self, data: st.DataObject) -> None:
        record = self._pick(data)
        if record is None:
            return
        task_id = record.task.task_id
        if record.result is None and self.now < record.task.deadline:
            try:
                self.service.expire(task_id, Decimal("0.01"))
            except ValueError:
                assert self.store.record(task_id).result is None
                event("截止时间以前记超时：报错")
                return
            raise AssertionError("截止时间以前记超时必须报错")

        result = self.service.expire(task_id, Decimal("0.01"))

        if self._settled_already(record, result):
            return
        assert result.status is TIMED_OUT
        assert result.elapsed == self.now - record.task.dispatched_at
        event("记超时")

    # 拨动时间

    @rule(seconds=st.integers(1, 200))
    def advance(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))

    @rule(data=st.data(), seconds=st.sampled_from([-1, 0, 1]))
    def to_a_deadline(self, data: st.DataObject, seconds: int) -> None:
        """拨到某个还在运行的任务的截止时间或它的前后一秒，碰截止时间的边界；只往后拨。"""
        running = [r for r in self._all() if r.result is None]
        if not running:
            return
        deadline = data.draw(st.sampled_from(running)).task.deadline
        target = deadline + timedelta(seconds=seconds)
        if target > self.now:
            self.clock.set(target)

    # 不变量

    @invariant()
    def within_the_limits(self) -> None:
        """PM 派发出去的不超过上限，轮次从 1 开始连续、不超过上限；Critic 不超过额度；用量一致。"""
        for case in CASES:
            records = self._records(case)
            dispatched = [r for r in records if not _rejected(r)]
            pm = [r for r in dispatched if r.task.dispatcher is PM]
            critic = [r for r in dispatched if r.task.dispatcher is CRITIC]
            rounds = {r.task.pm_round for r in pm}
            assert len(pm) <= CONFIG.max_pm_tasks
            assert len(critic) <= CONFIG.max_critic_tasks
            assert rounds == set(range(1, len(rounds) + 1)), "轮次是从 1 开始连续的数"
            assert len(rounds) <= CONFIG.max_pm_rounds
            assert all(r.task.pm_round is None for r in critic)
            found = usage(records)
            assert (found.pm_rounds, found.pm_tasks, found.critic_tasks) == (
                len(rounds),
                len(pm),
                len(critic),
            )
            assert dispatched_agents(records) == {r.task.target for r in dispatched}
            assert completed_results(records) == {
                str(r.task.task_id): r.task.target
                for r in records
                if r.result is not None and r.result.status is COMPLETED
            }

    @invariant()
    def targets_and_stocks(self) -> None:
        """寻股没有派给 News 的任务；单只股票的案件派发出去的任务都是案件的股票。"""
        for record in self._records(EXPLORATION):
            if not _rejected(record):
                assert record.task.target is not SubAgent.NEWS
        for record in self._records(SINGLE):
            if not _rejected(record):
                assert record.task.symbol == SINGLE.symbol

    @invariant()
    def one_reusable_same_task(self) -> None:
        """同一个案件里可以复用的相同任务最多一个。"""
        for case in CASES:
            reusable = [
                r.task
                for r in self._records(case)
                if r.result is None or r.result.status in _REUSABLE
            ]
            for i, a in enumerate(reusable):
                request = TaskRequest(
                    task_type=a.task_type, symbol=a.symbol, question=a.question, inputs=a.inputs
                )
                assert not any(_same(request, b) for b in reusable[i + 1 :])

    @invariant()
    def rejected_have_no_round(self) -> None:
        for record in self._all():
            if _rejected(record):
                assert record.task.pm_round is None

    @invariant()
    def one_result_per_task(self) -> None:
        """每个任务最多一个 Result，写入以后不变；晚于截止时间生成的 Result 都是超时。新看到的
        Result 都是这一步生成的，生成时刻就是现在。
        """
        records = self._all()
        assert [r.task.task_id for r in records] == self.task_ids
        for record in records:
            task, result = record.task, record.result
            if result is None:
                assert task.task_id not in self.results
                continue
            assert result.task_id == task.task_id
            if task.task_id in self.results:
                assert result == self.results[task.task_id]
                continue
            self.results[task.task_id] = result
            if result.status is REJECTED:
                assert (result.elapsed, result.cost) == (timedelta(0), Decimal(0))
                continue
            assert result.elapsed == self.now - task.dispatched_at
            if self.now > task.deadline:
                assert result.status is TIMED_OUT


TestTaskDispatchMachine = TaskDispatchMachine.TestCase
