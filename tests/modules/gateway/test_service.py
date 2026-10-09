"""任务派发用例的测试。每个测试对应 openspec 的 task-dispatch spec 里的一个场景，说明里写明场景。

基准数据见 tests/modules/gateway/builders.py：当前时刻 2026-10-14 15:00Z，T-n 就是 UUID(int=n)。
spec 一个 WHEN 里用"；"或"或者"隔开的几种情况各自从 GIVEN 开始，测试里各用一个新的用例。
"""

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from tests.fakes.clock import FakeClock
from tests.fakes.tasks import InMemoryTaskStore
from tests.modules.gateway.builders import (
    DEADLINE,
    EARNINGS_REQUEST,
    K1,
    K2,
    K3,
    K4,
    NEWS_REQUEST,
    NO_SIGNAL,
    NO_SIGNAL_JSON,
    NOW,
    PRICE_REQUEST,
    SIGNAL,
    SIGNAL_JSON,
    T1,
    T2,
    T3,
    T4,
    T5,
    T6,
    T7,
    at,
    numbered,
    request,
    signal,
    task_service,
)
from tradingagentspro.contracts.agent import SubAgent
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
    Dispatch,
    DispatchAction,
    TaskRecord,
    TaskUsage,
    completed_results,
    dispatched_agents,
    usage,
)

PM, CRITIC = Dispatcher.PM, Dispatcher.CRITIC
NEWS, QUANT, FUNDAMENTALS = SubAgent.NEWS, SubAgent.QUANT, SubAgent.FUNDAMENTALS
DISPATCHED, REUSED, REJECTED_ACTION = (
    DispatchAction.DISPATCHED,
    DispatchAction.REUSED,
    DispatchAction.REJECTED,
)
COMPLETED, NO_SIGNAL_STATUS, TIMED_OUT, FAILED, REJECTED = (
    ResultStatus.COMPLETED,
    ResultStatus.NO_SIGNAL,
    ResultStatus.TIMED_OUT,
    ResultStatus.FAILED,
    ResultStatus.REJECTED,
)
FORMAT_INVALID, CALL_FAILED = TaskFailure.FORMAT_INVALID, TaskFailure.CALL_FAILED
NOT_CASE_STOCK = TaskRejection.NOT_CASE_STOCK
EXPLORATION_TARGET = TaskRejection.EXPLORATION_TARGET
ROUNDS_USED_UP = TaskRejection.ROUNDS_USED_UP
PM_TASKS_USED_UP = TaskRejection.PM_TASKS_USED_UP
CRITIC_TASKS_USED_UP = TaskRejection.CRITIC_TASKS_USED_UP
HORIZON_4_JSON = signal(horizon=4).model_dump_json()  # "T-n 失败"收到的输出
RECEIVED_AT = at(15, 1, 30)

AAA_PRICE = request(PRICE_REQUEST, symbol="AAA")  # AAA 的价格请求
DDD_PRICE = request(PRICE_REQUEST, symbol="DDD")


class _Setup:
    """一个任务派发用例和它的时钟、存储。"""

    def __init__(self, **config: Any) -> None:
        self.clock = FakeClock(NOW)
        self.store = InMemoryTaskStore()
        self.service = task_service(self.clock, self.store, **config)

    def pm(self, *requests: TaskRequest, case: CaseScope = K1) -> tuple[Dispatch, ...]:
        """spec 里的"PM 派发 a、b"：PM 在 case 里调用一次 dispatch_tasks。"""
        return self.service.dispatch(case, PM, requests)

    def critic(self, *requests: TaskRequest, case: CaseScope = K1) -> tuple[Dispatch, ...]:
        return self.service.dispatch(case, CRITIC, requests)

    def complete(self, *task_ids: UUID) -> None:
        """spec 里的"T-n 完成"：现在收到基准信号。"""
        for task_id in task_ids:
            assert self.service.receive(task_id, SIGNAL_JSON, Decimal(0)).status is COMPLETED

    def no_signal(self, task_id: UUID) -> None:
        """spec 里的"T-n 无信号"：现在收到基准无信号。"""
        assert self.service.receive(task_id, NO_SIGNAL_JSON, Decimal(0)).status is NO_SIGNAL_STATUS

    def fail(self, task_id: UUID) -> None:
        """spec 里的"T-n 失败"：现在收到评估窗口 4 的信号。"""
        assert self.service.receive(task_id, HORIZON_4_JSON, Decimal(0)).status is FAILED

    def time_out(self, task_id: UUID) -> None:
        """spec 里的"T-n 超时"：到截止时间仍没有输出，记为超时；之后的派发仍在当前时刻。"""
        now = self.clock.now()
        self.clock.set(self.service.record(task_id).task.deadline)
        assert self.service.expire(task_id, Decimal(0)).status is TIMED_OUT
        self.clock.set(now)

    def at(self, moment: datetime) -> "_Setup":
        self.clock.set(moment)
        return self

    def result(self, task_id: UUID) -> Result | None:
        return self.service.record(task_id).result

    def usage(self, case: CaseScope = K1) -> TaskUsage:
        """spec 里的"PM 用了 r 轮、n 个任务"。"""
        return usage(self.service.records(case.case_id))


def _actions(dispatches: Sequence[Dispatch]) -> list[tuple[UUID, DispatchAction]]:
    """每个请求得到的 Task 和处理结果。"""
    return [(d.task.task_id, d.action) for d in dispatches]


def _rounds(dispatches: Sequence[Dispatch]) -> list[int | None]:
    return [d.task.pm_round for d in dispatches]


def _reason(setup: _Setup, dispatch: Dispatch) -> TaskRejection | None:
    """被拒绝的请求的原因：从存储取它的 Result。"""
    result = setup.result(dispatch.task.task_id)
    assert result is not None and result.status is REJECTED
    assert isinstance(result.reason, TaskRejection)
    return result.reason


def _pm_usage(rounds: int, tasks: int, critic: int = 0) -> TaskUsage:
    return TaskUsage(pm_rounds=rounds, pm_tasks=tasks, critic_tasks=critic)


def _prices(first: int, last: int) -> list[TaskRequest]:
    """价格请求 first 到 last。"""
    return [numbered(PRICE_REQUEST, n) for n in range(first, last + 1)]


# Task 的格式


def test_dispatched_task() -> None:
    """spec「派发出去的 Task」。"""
    setup = _Setup()

    dispatches = setup.pm(PRICE_REQUEST)

    assert dispatches == (
        Dispatch(
            Task(
                task_id=T1,
                case_id=K1.case_id,
                dispatcher=PM,
                task_type=TaskType.PRICE_REACTION,
                symbol="BBB",
                question="财报后的放量上涨能否持续？",
                inputs=("E-1",),
                dispatched_at=at(15),
                deadline=at(15, 5),
                pm_round=1,
            ),
            DISPATCHED,
        ),
    )
    assert dispatches[0].task.target is QUANT
    assert setup.result(T1) is None


def test_rejected_request_has_a_task() -> None:
    """spec「被拒绝的请求也有 Task」。"""
    setup = _Setup()

    (dispatch,) = setup.pm(AAA_PRICE)

    assert _actions([dispatch]) == [(T1, REJECTED_ACTION)]
    assert (dispatch.task.symbol, dispatch.task.pm_round) == ("AAA", None)
    assert setup.result(T1) == Result(
        task_id=T1, status=REJECTED, reason=NOT_CASE_STOCK, elapsed=timedelta(0), cost=Decimal(0)
    )
    assert setup.service.records(K1.case_id) == (TaskRecord(dispatch.task, setup.result(T1)),)


def test_critic_task_has_no_round() -> None:
    """spec「Critic 的任务没有轮次」。"""
    (dispatch,) = _Setup().critic(NEWS_REQUEST)

    assert _actions([dispatch]) == [(T1, DISPATCHED)]
    assert (dispatch.task.dispatcher, dispatch.task.target, dispatch.task.pm_round) == (
        CRITIC,
        NEWS,
        None,
    )


# Result 的格式


def test_completed_and_no_signal_results() -> None:
    """spec「完成和无信号的 Result」。"""
    completed = _Setup()
    completed.pm(PRICE_REQUEST)
    no_signal = _Setup()
    no_signal.pm(PRICE_REQUEST)

    signal_result = completed.at(RECEIVED_AT).service.receive(T1, SIGNAL_JSON, Decimal("0.02"))
    no_signal_result = no_signal.at(RECEIVED_AT).service.receive(
        T1, NO_SIGNAL_JSON, Decimal("0.02")
    )

    assert signal_result == Result(
        task_id=T1,
        status=COMPLETED,
        output=SIGNAL,
        elapsed=timedelta(seconds=90),
        cost=Decimal("0.02"),
    )
    assert signal_result.cited_entries == ("E-1", "E-2")
    assert (no_signal_result.status, no_signal_result.output) == (NO_SIGNAL_STATUS, NO_SIGNAL)
    assert no_signal_result.cited_entries == ()
    assert completed.result(T1) == signal_result


# 派发的顺序


def test_dispatching_several_tasks() -> None:
    """spec「一次派发几个任务」。"""
    setup = _Setup()

    dispatches = setup.pm(PRICE_REQUEST, NEWS_REQUEST, EARNINGS_REQUEST)

    assert _actions(dispatches) == [(T1, DISPATCHED), (T2, DISPATCHED), (T3, DISPATCHED)]
    assert [d.task.target for d in dispatches] == [QUANT, NEWS, FUNDAMENTALS]
    assert _rounds(dispatches) == [1, 1, 1]
    assert setup.usage() == _pm_usage(1, 3)


def test_some_dispatched_some_rejected() -> None:
    """spec「有的派发有的被拒绝」。"""
    setup = _Setup()

    dispatches = setup.pm(PRICE_REQUEST, request(NEWS_REQUEST, symbol="AAA"), EARNINGS_REQUEST)

    assert _actions(dispatches) == [(T1, DISPATCHED), (T2, REJECTED_ACTION), (T3, DISPATCHED)]
    assert _reason(setup, dispatches[1]) is NOT_CASE_STOCK
    assert setup.usage() == _pm_usage(1, 2)


def test_first_failing_check_for_a_single_stock_case() -> None:
    """spec「多项不通过时给出第一项」：轮次用完，股票也不是案件的股票。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.pm(NEWS_REQUEST)

    (dispatch,) = setup.pm(request(EARNINGS_REQUEST, symbol="AAA"))

    assert _reason(setup, dispatch) is NOT_CASE_STOCK


def test_first_failing_check_for_exploration() -> None:
    """spec「多项不通过时给出第一项」：寻股轮次用完，又派给 News。"""
    setup = _Setup()
    setup.pm(DDD_PRICE, case=K2)
    setup.pm(request(PRICE_REQUEST, symbol="EEE"), case=K2)

    (dispatch,) = setup.pm(request(NEWS_REQUEST, symbol="DDD"), case=K2)

    assert _reason(setup, dispatch) is EXPLORATION_TARGET


# 相同的任务


def test_reuse_a_completed_result() -> None:
    """spec「复用完成的 Result」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.complete(T1)

    dispatches = setup.pm(PRICE_REQUEST)

    assert _actions(dispatches) == [(T1, REUSED)]
    result = setup.result(T1)
    assert result is not None and result.status is COMPLETED
    assert len(setup.service.records(K1.case_id)) == 1
    assert setup.usage() == _pm_usage(1, 1)


def test_reuse_no_signal_with_inputs_in_another_order() -> None:
    """spec「复用无信号，输入的顺序不同」。"""
    setup = _Setup()
    setup.pm(EARNINGS_REQUEST)
    setup.no_signal(T1)

    dispatches = setup.pm(request(EARNINGS_REQUEST, inputs=("E-4", "E-3")))

    assert _actions(dispatches) == [(T1, REUSED)]


@pytest.mark.parametrize(
    "request_",
    [
        numbered(PRICE_REQUEST, 1),
        request(PRICE_REQUEST, inputs=("E-2",)),
        request(PRICE_REQUEST, inputs=("E-1", "E-2")),
        request(PRICE_REQUEST, task_type=TaskType.TREND_LEVELS),
    ],
    ids=["价格请求 1", "输入改为 E-2", "输入改为 E-1 和 E-2", "任务类型改为趋势和支撑阻力"],
)
def test_different_tasks_are_not_reused(request_: TaskRequest) -> None:
    """spec「不相同的任务不复用」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.complete(T1)

    dispatches = setup.pm(request_)

    assert _actions(dispatches) == [(T2, DISPATCHED)]
    assert _rounds(dispatches) == [2]


def test_duplicate_requests_in_one_dispatch() -> None:
    """spec「同一次里的重复请求」。"""
    setup = _Setup()

    dispatches = setup.pm(PRICE_REQUEST, PRICE_REQUEST)

    assert _actions(dispatches) == [(T1, DISPATCHED), (T1, REUSED)]
    assert setup.usage() == _pm_usage(1, 1)

    setup.complete(T1)

    results = [setup.result(d.task.task_id) for d in dispatches]
    assert results[0] == results[1]
    assert results[0] is not None and results[0].status is COMPLETED


def test_timed_out_task_is_not_reused() -> None:
    """spec「超时和失败的不复用」：T-1 超时。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.time_out(T1)

    dispatches = setup.pm(PRICE_REQUEST)

    assert _actions(dispatches) == [(T2, DISPATCHED)]
    assert _rounds(dispatches) == [2]
    assert setup.usage() == _pm_usage(2, 2)


def test_failed_task_is_not_reused() -> None:
    """spec「超时和失败的不复用」：T-1 失败。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.fail(T1)

    assert _actions(setup.pm(PRICE_REQUEST)) == [(T2, DISPATCHED)]


def test_rejected_task_is_not_reused() -> None:
    """spec「被拒绝的不复用」。"""
    setup = _Setup()
    setup.pm(AAA_PRICE)

    dispatches = setup.pm(AAA_PRICE)

    assert _actions(dispatches) == [(T2, REJECTED_ACTION)]
    assert _reason(setup, dispatches[0]) is NOT_CASE_STOCK


def test_critic_reuses_a_pm_task() -> None:
    """spec「Critic 复用 PM 的任务」。"""
    setup = _Setup()
    setup.pm(NEWS_REQUEST)
    setup.complete(T1)

    dispatches = setup.critic(NEWS_REQUEST)

    assert _actions(dispatches) == [(T1, REUSED)]
    assert setup.usage().critic_tasks == 0


def test_reuse_after_rounds_are_used_up() -> None:
    """spec「轮次用完以后仍可以复用」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.pm(NEWS_REQUEST)
    setup.complete(T1, T2)

    assert _actions(setup.pm(PRICE_REQUEST)) == [(T1, REUSED)]


def test_tasks_of_another_case_are_not_reused() -> None:
    """spec「不复用别的案件的任务」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST, case=K1)
    setup.complete(T1)

    dispatches = setup.pm(PRICE_REQUEST, case=K4)

    assert _actions(dispatches) == [(T2, DISPATCHED)]
    assert (dispatches[0].task.case_id, dispatches[0].task.pm_round) == (K4.case_id, 1)


# 案件的股票


def test_single_stock_case_dispatches_its_stock() -> None:
    """spec「单只股票的案件」：价格请求（股票 BBB）派发。"""
    assert _actions(_Setup().pm(PRICE_REQUEST)) == [(T1, DISPATCHED)]


@pytest.mark.parametrize(
    ("dispatcher", "request_"),
    [(PM, AAA_PRICE), (CRITIC, request(NEWS_REQUEST, symbol="AAA"))],
    ids=["PM 派发 AAA 的价格请求", "Critic 派发 AAA 的新闻请求"],
)
def test_single_stock_case_rejects_other_stocks(
    dispatcher: Dispatcher, request_: TaskRequest
) -> None:
    """spec「单只股票的案件」：都被拒绝，原因是股票不是案件的股票。"""
    setup = _Setup()

    (dispatch,) = setup.service.dispatch(K1, dispatcher, [request_])

    assert _reason(setup, dispatch) is NOT_CASE_STOCK


def test_exploration_dispatches_the_stocks_pm_names() -> None:
    """spec「寻股由 PM 指定股票」。"""
    dispatches = _Setup().pm(DDD_PRICE, request(EARNINGS_REQUEST, symbol="EEE"), case=K2)

    assert _actions(dispatches) == [(T1, DISPATCHED), (T2, DISPATCHED)]


# 寻股派给谁


@pytest.mark.parametrize(
    "request_",
    [
        request(NEWS_REQUEST, symbol="DDD"),
        request(NEWS_REQUEST, task_type=TaskType.NEWS_SINCE, symbol="DDD"),
    ],
    ids=["DDD 的新闻请求", "某日期以来的新闻汇总"],
)
def test_exploration_cannot_dispatch_to_news(request_: TaskRequest) -> None:
    """spec「寻股不能派给 News」。"""
    setup = _Setup()

    (dispatch,) = setup.pm(request_, case=K2)

    assert _reason(setup, dispatch) is EXPLORATION_TARGET


def test_exploration_dispatches_to_quant_and_fundamentals() -> None:
    """spec「寻股可以派给 Quant 和 Fundamentals」。"""
    dispatches = _Setup().pm(
        request(DDD_PRICE, task_type=TaskType.VOLATILITY),
        request(DDD_PRICE, task_type=TaskType.VALUATION),
        case=K2,
    )

    assert _actions(dispatches) == [(T1, DISPATCHED), (T2, DISPATCHED)]
    assert [d.task.target for d in dispatches] == [QUANT, FUNDAMENTALS]


@pytest.mark.parametrize(
    ("case", "symbol"), [(K1, "BBB"), (K3, "CCC")], ids=["K-1 的新闻请求", "K-3 的 CCC"]
)
def test_other_cases_dispatch_to_news(case: CaseScope, symbol: str) -> None:
    """spec「其他案件可以派给 News」。"""
    dispatches = _Setup().pm(request(NEWS_REQUEST, symbol=symbol), case=case)

    assert _actions(dispatches) == [(T1, DISPATCHED)]


# PM 的轮次


def test_no_dispatch_after_two_rounds() -> None:
    """spec「两轮以后不能再派」。"""
    setup = _Setup()
    assert _rounds(setup.pm(PRICE_REQUEST)) == [1]
    assert _rounds(setup.pm(NEWS_REQUEST)) == [2]

    dispatches = setup.pm(EARNINGS_REQUEST, numbered(PRICE_REQUEST, 1))

    assert [_reason(setup, d) for d in dispatches] == [ROUNDS_USED_UP, ROUNDS_USED_UP]
    assert setup.usage() == _pm_usage(2, 2)


def test_all_rejected_dispatch_is_not_a_round() -> None:
    """spec「全部被拒绝的一次不算一轮」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.pm(request(NEWS_REQUEST, symbol="AAA"))

    dispatches = setup.pm(NEWS_REQUEST)

    assert _actions(dispatches) == [(T3, DISPATCHED)]
    assert _rounds(dispatches) == [2]


def test_all_reused_dispatch_is_not_a_round() -> None:
    """spec「全部复用的一次不算一轮」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.complete(T1)
    assert _actions(setup.pm(PRICE_REQUEST)) == [(T1, REUSED)]

    dispatches = setup.pm(NEWS_REQUEST)

    assert _actions(dispatches) == [(T2, DISPATCHED)]
    assert _rounds(dispatches) == [2]


def test_reuse_and_dispatch_in_one_dispatch() -> None:
    """spec「一次里有复用也有派发」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.complete(T1)

    dispatches = setup.pm(PRICE_REQUEST, NEWS_REQUEST)

    assert _actions(dispatches) == [(T1, REUSED), (T2, DISPATCHED)]
    assert dispatches[1].task.pm_round == 2
    assert setup.usage() == _pm_usage(2, 2)


def test_rounds_do_not_depend_on_the_stage() -> None:
    """spec「不按阶段区分」：网关不知道案件阶段，Critic 审核、质询以后 PM 回应时派发照常计数。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    dispatches = setup.pm(NEWS_REQUEST)

    assert _actions(dispatches) == [(T2, DISPATCHED)]
    assert _rounds(dispatches) == [2]
    assert setup.usage() == _pm_usage(2, 2)


def test_critic_is_not_limited_by_rounds() -> None:
    """spec「Critic 不受轮次限制」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    setup.pm(NEWS_REQUEST)

    dispatches = setup.critic(numbered(NEWS_REQUEST, 1))

    assert _actions(dispatches) == [(T3, DISPATCHED)]
    assert _rounds(dispatches) == [None]
    assert setup.usage().pm_rounds == 2


# PM 的任务数


def test_pm_task_limit_boundary() -> None:
    """spec「任务数上限的边界」。"""
    setup = _Setup()

    first = setup.pm(*_prices(1, 6))
    seventh = setup.pm(numbered(PRICE_REQUEST, 7))

    assert [a for _, a in _actions(first)] == [DISPATCHED] * 6
    assert _reason(setup, seventh[0]) is PM_TASKS_USED_UP


def test_excess_in_one_dispatch_is_rejected() -> None:
    """spec「一次里超出的部分被拒绝」。"""
    setup = _Setup()

    dispatches = setup.pm(*_prices(1, 7))

    assert _actions(dispatches) == [
        (T1, DISPATCHED),
        (T2, DISPATCHED),
        (T3, DISPATCHED),
        (T4, DISPATCHED),
        (T5, DISPATCHED),
        (T6, DISPATCHED),
        (T7, REJECTED_ACTION),
    ]
    assert _reason(setup, dispatches[6]) is PM_TASKS_USED_UP
    assert setup.usage() == _pm_usage(1, 6)


def test_second_round_partly_over_the_limit() -> None:
    """spec「第二轮部分超限」。"""
    setup = _Setup()
    setup.pm(*_prices(1, 4))

    dispatches = setup.pm(*_prices(5, 7))

    assert [a for _, a in _actions(dispatches)] == [DISPATCHED, DISPATCHED, REJECTED_ACTION]
    assert _rounds(dispatches) == [2, 2, None]
    assert _reason(setup, dispatches[2]) is PM_TASKS_USED_UP


def test_timed_out_and_failed_tasks_count() -> None:
    """spec「超时和失败的任务也算」。"""
    setup = _Setup()
    setup.pm(*_prices(1, 6))
    setup.time_out(T1)
    setup.fail(T2)

    (dispatch,) = setup.pm(numbered(PRICE_REQUEST, 7))

    assert _reason(setup, dispatch) is PM_TASKS_USED_UP


def test_rejected_tasks_do_not_count() -> None:
    """spec「被拒绝的任务不算」。"""
    setup = _Setup()
    setup.pm(AAA_PRICE)

    dispatches = setup.pm(*_prices(1, 6))

    assert [a for _, a in _actions(dispatches)] == [DISPATCHED] * 6
    assert _rounds(dispatches) == [1] * 6


# Critic 的补充研究额度


def test_critic_dispatches_after_pm_uses_everything() -> None:
    """spec「PM 用满以后 Critic 仍能派发」。"""
    setup = _Setup()
    setup.pm(*_prices(1, 3))
    setup.pm(*_prices(4, 6))

    dispatches = setup.critic(numbered(NEWS_REQUEST, 1))

    assert _actions(dispatches) == [(T7, DISPATCHED)]
    assert setup.usage() == _pm_usage(2, 6, critic=1)


def test_critic_quota_boundary() -> None:
    """spec「补充研究额度的边界」。"""
    setup = _Setup()

    first = setup.critic(numbered(NEWS_REQUEST, 1), numbered(NEWS_REQUEST, 2))
    third = setup.critic(numbered(NEWS_REQUEST, 3))

    assert _actions(first) == [(T1, DISPATCHED), (T2, DISPATCHED)]
    assert _reason(setup, third[0]) is CRITIC_TASKS_USED_UP


def test_critic_quota_counts_per_case() -> None:
    """spec「额度按案件累计」：三次审核各派发一次。"""
    setup = _Setup()
    setup.critic(numbered(NEWS_REQUEST, 1))
    setup.critic(numbered(NEWS_REQUEST, 2))

    (dispatch,) = setup.critic(numbered(NEWS_REQUEST, 3))

    assert _reason(setup, dispatch) is CRITIC_TASKS_USED_UP


# 子 agent 的输出变成 Result


@pytest.mark.parametrize(
    ("output", "status", "expected"),
    [(SIGNAL_JSON, COMPLETED, SIGNAL), (NO_SIGNAL_JSON, NO_SIGNAL_STATUS, NO_SIGNAL)],
    ids=["基准信号", "基准无信号"],
)
def test_valid_signal_and_no_signal(output: str, status: ResultStatus, expected: object) -> None:
    """spec「收到合法的信号和无信号」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    result = setup.at(RECEIVED_AT).service.receive(T1, output, Decimal("0.02"))

    assert (result.status, result.output) == (status, expected)
    assert setup.result(T1) == result


def _without_risks() -> str:
    fields = json.loads(SIGNAL_JSON)
    del fields["risks"]
    return json.dumps(fields)


def test_structure_error_names_the_item() -> None:
    """spec「结构错误」：缺少主要风险的基准信号，说明指出缺少主要风险。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    result = setup.at(RECEIVED_AT).service.receive(T1, _without_risks(), Decimal("0.02"))

    assert (result.status, result.reason) == (FAILED, FORMAT_INVALID)
    assert result.detail is not None
    assert "Signal.risks: Field required" in result.detail.split("; ")
    assert result.output is None


@pytest.mark.parametrize(
    ("output", "detail"),
    [
        ("看多，理由见上", "输出: Invalid JSON: expected value at line 1 column 1"),
        (
            json.dumps(json.loads(NO_SIGNAL_JSON) | {"score": 0}),
            "NoSignal.score: Extra inputs are not permitted",
        ),
    ],
    ids=["不是 JSON", "无信号写了评分 0"],
)
def test_other_structure_errors(output: str, detail: str) -> None:
    """spec「结构错误」：不是 JSON 的文字；基准无信号另外写了评分 0。都失败，原因是格式不合法。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    result = setup.at(RECEIVED_AT).service.receive(T1, output, Decimal("0.02"))

    assert (result.status, result.reason) == (FAILED, FORMAT_INVALID)
    assert result.detail is not None and detail in result.detail.split("; ")


def test_horizon_not_in_the_options() -> None:
    """spec「评估窗口不在配置的选项里」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    result = setup.at(RECEIVED_AT).service.receive(T1, HORIZON_4_JSON, Decimal("0.02"))

    assert (result.status, result.reason, result.detail) == (
        FAILED,
        FORMAT_INVALID,
        "horizon_not_offered",
    )


@pytest.mark.parametrize(
    ("data_as_of", "status", "detail"),
    [(at(15, 1, 31), FAILED, "data_after_now"), (at(15, 1, 30), COMPLETED, None)],
    ids=["15:01:31Z", "15:01:30Z"],
)
def test_data_as_of_after_receipt(
    data_as_of: datetime, status: ResultStatus, detail: str | None
) -> None:
    """spec「数据截至时刻晚于收到的时刻」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    output = signal(data_as_of=data_as_of).model_dump_json()

    result = setup.at(RECEIVED_AT).service.receive(T1, output, Decimal("0.02"))

    assert (result.status, result.detail) == (status, detail)


# 超时


@pytest.mark.parametrize(
    ("moment", "status", "output"),
    [(at(15, 5), COMPLETED, SIGNAL), (at(15, 5, 1), TIMED_OUT, None)],
    ids=["15:05:00Z", "15:05:01Z"],
)
def test_deadline_boundary(moment: datetime, status: ResultStatus, output: object) -> None:
    """spec「截止时间的边界」。"""
    setup = _Setup()
    (dispatch,) = setup.pm(PRICE_REQUEST)
    assert dispatch.task.deadline == DEADLINE

    result = setup.at(moment).service.receive(T1, SIGNAL_JSON, Decimal("0.02"))

    assert (result.status, result.output) == (status, output)


def test_no_output_by_the_deadline() -> None:
    """spec「到截止时间仍没有输出」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    result = setup.at(at(15, 5)).service.expire(T1, Decimal("0.01"))

    assert result == Result(
        task_id=T1, status=TIMED_OUT, elapsed=timedelta(minutes=5), cost=Decimal("0.01")
    )
    assert setup.result(T1) == result


def test_cannot_expire_before_the_deadline() -> None:
    """spec「截止时间以前不能记超时」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    with pytest.raises(ValueError, match="还没到截止时间"):
        setup.at(at(15, 4, 59)).service.expire(T1, Decimal("0.01"))

    assert setup.result(T1) is None


def test_call_failure_after_the_deadline() -> None:
    """spec「截止时间以后的调用失败」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    result = setup.at(at(15, 5, 1)).service.fail(T1, "主模型和备用模型都失败", Decimal(0))

    assert result.status is TIMED_OUT


# 调用失败


def test_call_failed() -> None:
    """spec「调用失败」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)

    result = setup.at(at(15, 2)).service.fail(T1, "主模型和备用模型都失败", Decimal(0))

    assert result == Result(
        task_id=T1,
        status=FAILED,
        reason=CALL_FAILED,
        detail="主模型和备用模型都失败",
        elapsed=timedelta(minutes=2),
        cost=Decimal(0),
    )
    assert setup.result(T1) == result


# 一个任务只有一个 Result


def test_output_after_timing_out() -> None:
    """spec「超时以后才收到输出」：同样在 15:05:00Z 收到基准信号，T-1 仍是超时。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    timed_out = setup.at(at(15, 5)).service.expire(T1, Decimal("0.01"))

    result = setup.service.receive(T1, SIGNAL_JSON, Decimal("0.02"))

    assert result == timed_out
    assert setup.result(T1) == timed_out
    assert timed_out.output is None


_LATER: list[Callable[[_Setup], Result]] = [
    lambda s: s.at(at(15, 2)).service.receive(
        T1, signal(horizon=20).model_dump_json(), Decimal("0.03")
    ),
    lambda s: s.at(at(15, 2)).service.fail(T1, "主模型和备用模型都失败", Decimal(0)),
    lambda s: s.at(at(15, 5)).service.expire(T1, Decimal("0.01")),
]


@pytest.mark.parametrize(
    "later", _LATER, ids=["15:02 收到评估窗口 20 的信号", "15:02 调用失败", "15:05 记超时"]
)
def test_nothing_changes_after_completion(later: Callable[[_Setup], Result]) -> None:
    """spec「完成以后再收到」：T-1 的 Result 都不变。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST)
    completed = setup.at(RECEIVED_AT).service.receive(T1, SIGNAL_JSON, Decimal("0.02"))

    result = later(setup)

    assert result == completed
    assert setup.result(T1) == completed
    assert (completed.status, completed.output) == (COMPLETED, SIGNAL)


def test_rejected_task_keeps_its_result() -> None:
    """spec「被拒绝的任务」：收到基准信号，T-1 仍是被拒绝。"""
    setup = _Setup()
    setup.pm(AAA_PRICE)

    result = setup.service.receive(T1, SIGNAL_JSON, Decimal("0.02"))

    assert (result.status, result.reason) == (REJECTED, NOT_CASE_STOCK)
    assert setup.result(T1) == result


# 本案件的任务和用量


def test_dispatched_agents_and_completed_results() -> None:
    """spec「派过任务的子 agent 和完成的 Result」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST, NEWS_REQUEST, request(EARNINGS_REQUEST, symbol="AAA"))
    setup.complete(T1)
    setup.time_out(T2)

    records = setup.service.records(K1.case_id)

    statuses = [(r.task.task_id, r.result.status if r.result else None) for r in records]
    assert statuses == [(T1, COMPLETED), (T2, TIMED_OUT), (T3, REJECTED)]
    assert dispatched_agents(records) == {QUANT, NEWS}
    assert completed_results(records) == {str(T1): QUANT}
    assert usage(records) == _pm_usage(1, 2, critic=0)


def test_running_and_no_signal_tasks() -> None:
    """spec「还在运行和无信号的任务」。"""
    setup = _Setup()
    setup.pm(PRICE_REQUEST, EARNINGS_REQUEST)
    setup.no_signal(T1)

    records = setup.service.records(K1.case_id)

    assert records[1].result is None
    assert dispatched_agents(records) == {QUANT, FUNDAMENTALS}
    assert completed_results(records) == {}


def test_cases_do_not_affect_each_other() -> None:
    """spec「案件之间互不影响」。"""
    setup = _Setup()
    setup.pm(*_prices(1, 3))
    setup.pm(*_prices(4, 6))

    dispatches = setup.pm(DDD_PRICE, case=K2)

    assert _actions(dispatches) == [(T7, DISPATCHED)]
    assert _rounds(dispatches) == [1]
    assert setup.usage(K2) == _pm_usage(1, 1)
    assert setup.usage(K1) == _pm_usage(2, 6)
    k3 = setup.service.records(K3.case_id)
    assert k3 == ()
    assert usage(k3) == _pm_usage(0, 0)
    assert dispatched_agents(k3) == frozenset()


# 派发的配置


def test_other_round_limit() -> None:
    """spec「改成别的值」：PM 的轮次上限配置为 1。"""
    setup = _Setup(max_pm_rounds=1)
    setup.pm(PRICE_REQUEST)

    (dispatch,) = setup.pm(NEWS_REQUEST)

    assert _reason(setup, dispatch) is ROUNDS_USED_UP


def test_other_pm_task_limit() -> None:
    """spec「改成别的值」：PM 的任务数上限配置为 1。"""
    setup = _Setup(max_pm_tasks=1)

    dispatches = setup.pm(PRICE_REQUEST, NEWS_REQUEST)

    assert _actions(dispatches) == [(T1, DISPATCHED), (T2, REJECTED_ACTION)]
    assert _reason(setup, dispatches[1]) is PM_TASKS_USED_UP


def test_other_critic_quota() -> None:
    """spec「改成别的值」：Critic 的补充研究额度配置为 1。"""
    setup = _Setup(max_critic_tasks=1)

    dispatches = setup.critic(numbered(NEWS_REQUEST, 1), numbered(NEWS_REQUEST, 2))

    assert _actions(dispatches) == [(T1, DISPATCHED), (T2, REJECTED_ACTION)]
    assert _reason(setup, dispatches[1]) is CRITIC_TASKS_USED_UP


def test_other_task_timeout() -> None:
    """spec「改成别的值」：任务时限配置为 10 分钟。"""
    (dispatch,) = _Setup(task_timeout=timedelta(minutes=10)).pm(PRICE_REQUEST)

    assert dispatch.task.deadline == at(15, 10)
