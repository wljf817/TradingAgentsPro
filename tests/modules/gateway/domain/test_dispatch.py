"""派发规则的测试：相同的任务、复用、派发的检查、用量和给结论校验的数，直接构造本案件的记录。

规则见 openspec 的 task-dispatch spec「派发的顺序」「相同的任务」「案件的股票」「寻股派给谁」
「PM 的轮次」「PM 的任务数」「Critic 的补充研究额度」「本案件的任务和用量」；用例层的场景见
tests/modules/gateway/test_service.py。基准数据见 tests/modules/gateway/builders.py。
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from tests.modules.gateway.builders import (
    DEADLINE,
    EARNINGS_REQUEST,
    K1,
    K2,
    K3,
    NEWS_REQUEST,
    NOW,
    PRICE_REQUEST,
    T1,
    T2,
    T3,
    at,
    numbered,
    record,
    request,
    task,
)
from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.task import (
    Dispatcher,
    ResultStatus,
    Task,
    TaskRejection,
    TaskRequest,
    TaskType,
)
from tradingagentspro.modules.gateway import (
    CaseScope,
    GatewayConfig,
    TaskRecord,
    TaskUsage,
    completed_results,
    dispatch_rejection,
    dispatched_agents,
    new_task,
    reusable,
    rounds_used,
    same_task,
    tasks_used,
    usage,
)

PM, CRITIC = Dispatcher.PM, Dispatcher.CRITIC
NEWS, QUANT, FUNDAMENTALS = SubAgent.NEWS, SubAgent.QUANT, SubAgent.FUNDAMENTALS
COMPLETED, NO_SIGNAL, TIMED_OUT, FAILED, REJECTED = (
    ResultStatus.COMPLETED,
    ResultStatus.NO_SIGNAL,
    ResultStatus.TIMED_OUT,
    ResultStatus.FAILED,
    ResultStatus.REJECTED,
)
NOT_CASE_STOCK = TaskRejection.NOT_CASE_STOCK
EXPLORATION_TARGET = TaskRejection.EXPLORATION_TARGET
ROUNDS_USED_UP = TaskRejection.ROUNDS_USED_UP
PM_TASKS_USED_UP = TaskRejection.PM_TASKS_USED_UP
CRITIC_TASKS_USED_UP = TaskRejection.CRITIC_TASKS_USED_UP
CONFIG = GatewayConfig()


def _pm_tasks(count: int, status: ResultStatus | None = COMPLETED) -> list[TaskRecord]:
    """PM 在 K-1 派发出去的价格请求 1 到 count，第 1 轮。"""
    return [
        record(task(numbered(PRICE_REQUEST, n), task_id=UUID(int=n)), status)
        for n in range(1, count + 1)
    ]


def _critic_tasks(count: int, status: ResultStatus | None = COMPLETED) -> list[TaskRecord]:
    """Critic 在 K-1 派发出去的新闻请求 1 到 count。"""
    return [
        record(
            task(numbered(NEWS_REQUEST, n), task_id=UUID(int=100 + n), dispatcher=CRITIC), status
        )
        for n in range(1, count + 1)
    ]


def _rejected(count: int, dispatcher: Dispatcher = PM) -> list[TaskRecord]:
    """在 K-1 被拒绝的 AAA 的价格请求 1 到 count：没有轮次。"""
    return [
        record(
            task(
                request(numbered(PRICE_REQUEST, n), symbol="AAA"),
                task_id=UUID(int=200 + n),
                dispatcher=dispatcher,
                pm_round=None,
            ),
            REJECTED,
        )
        for n in range(1, count + 1)
    ]


def _reject(
    request_: TaskRequest,
    *,
    dispatcher: Dispatcher = PM,
    scope: CaseScope = K1,
    records: Sequence[TaskRecord] = (),
    batch_round: int = 1,
    config: GatewayConfig = CONFIG,
) -> TaskRejection | None:
    return dispatch_rejection(request_, dispatcher, scope, records, batch_round, config)


# 相同的任务


@pytest.mark.parametrize(
    "changes",
    [
        {"task_type": TaskType.TREND_LEVELS},
        {"symbol": "AAA"},
        {"question": "问题 1"},
        {"inputs": ("E-2",)},
        {"inputs": ("E-1", "E-2")},
        {"inputs": ()},
    ],
    ids=["任务类型", "股票", "问题", "输入不同", "多一个输入", "没有输入"],
)
def test_one_item_differs(changes: dict[str, Any]) -> None:
    """spec「相同的任务」：任务类型、股票、问题、输入有一项不同就不相同。"""
    assert not same_task(request(PRICE_REQUEST, **changes), task(PRICE_REQUEST))


def test_inputs_in_another_order_and_another_dispatcher_are_the_same() -> None:
    """spec「相同的任务」：输入的条目不论顺序，不论发起者。"""
    earnings = task(EARNINGS_REQUEST, dispatcher=CRITIC)

    assert same_task(request(EARNINGS_REQUEST, inputs=("E-4", "E-3")), earnings)
    assert same_task(PRICE_REQUEST, task(PRICE_REQUEST))


# 复用


@pytest.mark.parametrize("status", [None, COMPLETED, NO_SIGNAL], ids=["还在运行", "完成", "无信号"])
def test_reusable_results(status: ResultStatus | None) -> None:
    """spec「相同的任务」：还没有 Result，或者 Result 是完成、无信号的相同任务复用。"""
    t1 = task(PRICE_REQUEST)

    assert reusable(PRICE_REQUEST, [record(t1, status)]) == t1


@pytest.mark.parametrize("status", [TIMED_OUT, FAILED, REJECTED], ids=["超时", "失败", "被拒绝"])
def test_results_not_reused(status: ResultStatus) -> None:
    """spec「相同的任务」：Result 是超时、失败或被拒绝的任务不复用。"""
    assert reusable(PRICE_REQUEST, [record(task(PRICE_REQUEST), status)]) is None


def test_reuses_the_reusable_one_among_same_tasks() -> None:
    """相同的任务先超时、再派发完成：复用后来完成的那个。"""
    timed_out = record(task(PRICE_REQUEST, task_id=T1), TIMED_OUT)
    completed = record(task(PRICE_REQUEST, task_id=T2, pm_round=2), COMPLETED)

    assert reusable(PRICE_REQUEST, [timed_out, completed]) == completed.task


def test_different_tasks_not_reused() -> None:
    records = [record(task(PRICE_REQUEST), COMPLETED), record(task(NEWS_REQUEST, task_id=T2))]

    assert reusable(numbered(PRICE_REQUEST, 1), records) is None
    assert reusable(EARNINGS_REQUEST, records) is None
    assert reusable(PRICE_REQUEST, []) is None


# 派发的检查：1 案件的股票


@pytest.mark.parametrize("dispatcher", [PM, CRITIC])
def test_case_stock(dispatcher: Dispatcher) -> None:
    """spec「派发的顺序」第 1 行：单只股票的案件，PM 和 Critic 都只能派这只股票的任务。"""
    assert _reject(PRICE_REQUEST, dispatcher=dispatcher) is None
    assert _reject(request(NEWS_REQUEST, symbol="AAA"), dispatcher=dispatcher) is NOT_CASE_STOCK
    assert _reject(request(NEWS_REQUEST, symbol="CCC"), dispatcher=dispatcher, scope=K3) is None
    assert _reject(PRICE_REQUEST, dispatcher=dispatcher, scope=K3) is NOT_CASE_STOCK


def test_symbol_case_is_not_normalized() -> None:
    """股票代码不规范化：小写的案件股票不是案件的股票。"""
    assert _reject(request(PRICE_REQUEST, symbol="bbb")) is NOT_CASE_STOCK


@pytest.mark.parametrize("symbol", ["DDD", "EEE", "BBB"])
def test_exploration_does_not_check_the_stock(symbol: str) -> None:
    """spec「寻股由 PM 指定股票」：寻股没有案件的股票，不检查。"""
    assert _reject(request(PRICE_REQUEST, symbol=symbol), scope=K2) is None


# 派发的检查：2 寻股派给谁


@pytest.mark.parametrize("dispatcher", [PM, CRITIC])
@pytest.mark.parametrize(
    ("task_type", "rejection"),
    [
        (TaskType.EVENT_IMPACT, EXPLORATION_TARGET),
        (TaskType.NEWS_SINCE, EXPLORATION_TARGET),
        (TaskType.PRICE_REACTION, None),
        (TaskType.TREND_LEVELS, None),
        (TaskType.VOLATILITY, None),
        (TaskType.EARNINGS_VS_EXPECTATIONS, None),
        (TaskType.GUIDANCE_CHANGE, None),
        (TaskType.VALUATION, None),
    ],
)
def test_exploration_targets(
    dispatcher: Dispatcher, task_type: TaskType, rejection: TaskRejection | None
) -> None:
    """spec「派发的顺序」第 2 行：寻股只能派给 Quant 和 Fundamentals。"""
    ddd = request(PRICE_REQUEST, task_type=task_type, symbol="DDD")

    assert _reject(ddd, dispatcher=dispatcher, scope=K2) is rejection


@pytest.mark.parametrize(
    ("scope", "symbol"), [(K1, "BBB"), (K3, "CCC")], ids=["关注股事件", "持仓事件"]
)
def test_other_cases_can_dispatch_to_news(scope: CaseScope, symbol: str) -> None:
    """spec「其他案件可以派给 News」。"""
    assert _reject(request(NEWS_REQUEST, symbol=symbol), scope=scope) is None


# 派发的检查：3 PM 的轮次


@pytest.mark.parametrize(("batch_round", "rejection"), [(1, None), (2, None), (3, ROUNDS_USED_UP)])
def test_pm_round_limit(batch_round: int, rejection: TaskRejection | None) -> None:
    """spec「派发的顺序」第 3 行：这一次的轮次不超过 PM 的轮次上限 2。"""
    assert _reject(NEWS_REQUEST, batch_round=batch_round) is rejection


def test_critic_does_not_check_rounds() -> None:
    """spec「Critic 不受轮次限制」：PM 已经用掉 2 轮，Critic 这一次的轮次不看。"""
    records = _pm_tasks(2)

    assert _reject(NEWS_REQUEST, dispatcher=CRITIC, records=records, batch_round=3) is None


def test_other_round_limit() -> None:
    """spec「改成别的值」：PM 的轮次上限 1，第 2 轮被拒绝。"""
    config = GatewayConfig(max_pm_rounds=1)

    assert _reject(NEWS_REQUEST, batch_round=1, config=config) is None
    assert _reject(NEWS_REQUEST, batch_round=2, config=config) is ROUNDS_USED_UP


# 派发的检查：4 PM 的任务数


@pytest.mark.parametrize(("count", "rejection"), [(5, None), (6, PM_TASKS_USED_UP)])
@pytest.mark.parametrize("status", [None, COMPLETED, NO_SIGNAL, TIMED_OUT, FAILED], ids=str)
def test_pm_task_limit(
    count: int, rejection: TaskRejection | None, status: ResultStatus | None
) -> None:
    """spec「派发的顺序」第 4 行：派发出去的少于 6 个才通过，不论 Result 的状态，还在运行的也算。"""
    records = _pm_tasks(count, status)

    assert _reject(NEWS_REQUEST, records=records) is rejection


def test_rejected_and_critic_tasks_do_not_count_for_pm() -> None:
    """spec「被拒绝的任务不算」：PM 派发出去 5 个，另有被拒绝的 3 个和 Critic 的 2 个。"""
    records = [*_pm_tasks(5), *_rejected(3), *_critic_tasks(2)]

    assert _reject(NEWS_REQUEST, records=records) is None


def test_other_pm_task_limit() -> None:
    """spec「改成别的值」：PM 的任务数上限 1。"""
    config = GatewayConfig(max_pm_tasks=1)

    assert _reject(PRICE_REQUEST, config=config) is None
    assert _reject(NEWS_REQUEST, records=_pm_tasks(1, None), config=config) is PM_TASKS_USED_UP


# 派发的检查：5 Critic 的补充研究额度


@pytest.mark.parametrize(("count", "rejection"), [(1, None), (2, CRITIC_TASKS_USED_UP)])
@pytest.mark.parametrize("status", [None, COMPLETED, TIMED_OUT, FAILED], ids=str)
def test_critic_task_limit(
    count: int, rejection: TaskRejection | None, status: ResultStatus | None
) -> None:
    """spec「派发的顺序」第 5 行：Critic 派发出去的少于 2 个才通过，不论 Result 的状态。"""
    records = _critic_tasks(count, status)

    assert _reject(numbered(NEWS_REQUEST, 9), dispatcher=CRITIC, records=records) is rejection


def test_pm_tasks_and_rejected_do_not_count_for_critic() -> None:
    """spec「PM 用满以后 Critic 仍能派发」：PM 用满 6 个任务，Critic 被拒绝的 1 个也不算。"""
    records = [*_pm_tasks(6), *_rejected(1, CRITIC), *_critic_tasks(1)]

    assert _reject(NEWS_REQUEST, dispatcher=CRITIC, records=records, batch_round=3) is None


def test_other_critic_task_limit() -> None:
    """spec「改成别的值」：Critic 的补充研究额度 1。"""
    config = GatewayConfig(max_critic_tasks=1)

    assert _reject(NEWS_REQUEST, dispatcher=CRITIC, config=config) is None
    records = _critic_tasks(1)
    assert _reject(NEWS_REQUEST, dispatcher=CRITIC, records=records, config=config) is (
        CRITIC_TASKS_USED_UP
    )


# 多项不通过时给出第一项


@pytest.mark.parametrize(
    ("request_", "dispatcher", "scope", "batch_round", "rejection"),
    [
        (request(EARNINGS_REQUEST, symbol="AAA"), PM, K1, 3, NOT_CASE_STOCK),
        (request(NEWS_REQUEST, symbol="DDD"), PM, K2, 3, EXPLORATION_TARGET),
        (NEWS_REQUEST, PM, K1, 3, ROUNDS_USED_UP),
        (request(NEWS_REQUEST, symbol="AAA"), CRITIC, K1, 1, NOT_CASE_STOCK),
        (request(NEWS_REQUEST, symbol="DDD"), CRITIC, K2, 1, EXPLORATION_TARGET),
    ],
    ids=[
        "股票、轮次和任务数",
        "寻股派给 News、轮次和任务数",
        "轮次和任务数",
        "Critic：股票和额度",
        "Critic：寻股派给 News 和额度",
    ],
)
def test_first_failing_check(
    request_: TaskRequest,
    dispatcher: Dispatcher,
    scope: CaseScope,
    batch_round: int,
    rejection: TaskRejection,
) -> None:
    """spec「多项不通过时给出第一项」：PM 已经派发出去 6 个任务，Critic 已经派发出去 2 个。"""
    records = [*_pm_tasks(6), *_critic_tasks(2)]

    assert (
        _reject(
            request_, dispatcher=dispatcher, scope=scope, records=records, batch_round=batch_round
        )
        is rejection
    )


# 用量


def test_rounds_used() -> None:
    """PM 用掉的轮次：派发出去的任务里最大的轮次；被拒绝的和 Critic 的任务没有轮次。"""
    first = record(task(PRICE_REQUEST, task_id=T1, pm_round=1), COMPLETED)
    second = record(task(NEWS_REQUEST, task_id=T2, pm_round=2))

    assert rounds_used([]) == 0
    assert rounds_used([*_rejected(2), *_critic_tasks(2)]) == 0
    assert rounds_used([first, *_rejected(1)]) == 1
    assert rounds_used([first, second]) == 2


def test_tasks_used_by_each_dispatcher() -> None:
    records = [*_pm_tasks(2, TIMED_OUT), *_pm_tasks(1, None), *_rejected(2), *_critic_tasks(1)]

    assert tasks_used(records, PM) == 3
    assert tasks_used(records, CRITIC) == 1


def test_dispatched_agents_and_completed_results() -> None:
    """spec「派过任务的子 agent 和完成的 Result」：T-1 完成，T-2 超时，T-3（AAA 的财报请求）
    被拒绝。
    """
    records = [
        record(task(PRICE_REQUEST, task_id=T1), COMPLETED),
        record(task(NEWS_REQUEST, task_id=T2), TIMED_OUT),
        record(task(request(EARNINGS_REQUEST, symbol="AAA"), task_id=T3, pm_round=None), REJECTED),
    ]

    assert dispatched_agents(records) == {QUANT, NEWS}
    assert completed_results(records) == {str(T1): QUANT}
    assert usage(records) == TaskUsage(pm_rounds=1, pm_tasks=2, critic_tasks=0)


def test_running_and_no_signal_tasks() -> None:
    """spec「还在运行和无信号的任务」：T-1 无信号，T-2 还没有 Result。"""
    records = [
        record(task(PRICE_REQUEST, task_id=T1), NO_SIGNAL),
        record(task(EARNINGS_REQUEST, task_id=T2)),
    ]

    assert dispatched_agents(records) == {QUANT, FUNDAMENTALS}
    assert completed_results(records) == {}


def test_rejected_tasks_are_not_dispatched() -> None:
    """case-types spec「被拒绝的任务不算派过」：派给 Fundamentals 的任务被拒绝，派发出去的只有
    News 和 Quant 的任务。
    """
    records = [
        record(task(NEWS_REQUEST, task_id=T1), COMPLETED),
        record(task(PRICE_REQUEST, task_id=T2), FAILED),
        record(task(request(EARNINGS_REQUEST, symbol="AAA"), task_id=T3, pm_round=None), REJECTED),
    ]

    assert dispatched_agents(records) == {NEWS, QUANT}
    assert FUNDAMENTALS not in dispatched_agents(records)


def test_usage_of_a_full_case_and_an_empty_case() -> None:
    """spec「案件之间互不影响」的数：K-1 用满 2 轮、6 个任务；没有任务的案件用量都是 0。"""
    first = _pm_tasks(3)
    second = [
        record(task(numbered(PRICE_REQUEST, n), task_id=UUID(int=n), pm_round=2), None)
        for n in range(4, 7)
    ]

    assert usage([*first, *second]) == TaskUsage(pm_rounds=2, pm_tasks=6, critic_tasks=0)
    assert usage([]) == TaskUsage(pm_rounds=0, pm_tasks=0, critic_tasks=0)
    assert dispatched_agents([]) == frozenset()
    assert completed_results([]) == {}


def test_critic_usage() -> None:
    records = [*_critic_tasks(2, None), *_rejected(1, CRITIC)]

    assert usage(records) == TaskUsage(pm_rounds=0, pm_tasks=0, critic_tasks=2)


# 新的 Task


def test_new_task_copies_the_request() -> None:
    """spec「派发出去的 Task」：照抄请求的四项，截止时间是派发时刻加任务时限 5 分钟。"""
    t1 = new_task(T1, K1, PM, PRICE_REQUEST, NOW, 1, CONFIG)

    assert t1 == Task(
        task_id=T1,
        case_id=K1.case_id,
        dispatcher=PM,
        task_type=TaskType.PRICE_REACTION,
        symbol="BBB",
        question="财报后的放量上涨能否持续？",
        inputs=("E-1",),
        dispatched_at=NOW,
        deadline=DEADLINE,
        pm_round=1,
    )
    assert t1.target is QUANT


def test_new_task_round_and_timeout() -> None:
    """轮次按传入的；spec「改成别的值」：任务时限 10 分钟时截止时间 15:10Z。"""
    config = GatewayConfig(task_timeout=timedelta(minutes=10))

    critic = new_task(T2, K2, CRITIC, request(PRICE_REQUEST, symbol="DDD"), NOW, None, config)
    second = new_task(T3, K1, PM, NEWS_REQUEST, NOW, 2, CONFIG)

    assert (critic.case_id, critic.dispatcher, critic.symbol) == (K2.case_id, CRITIC, "DDD")
    assert (critic.pm_round, critic.deadline) == (None, at(15, 10))
    assert second.pm_round == 2
