"""调用预算用例的测试。每个测试对应 openspec 的 call-budget spec 里的一个场景，说明里写明场景
（「模型的单价」和「预算的配置」的前两个场景在 test_config.py，「按单价算」「不舍入」在
domain/test_budget.py）。

基准数据见 tests/modules/gateway/builders.py：当前时刻 2026-10-14 15:00Z（周三，盘中），纽交所样例
日历，基准模型的配置和基准单价；K-1 到 K-8 是固定的 UUID。"K-n 记下 x"是 builders 的 spend：一次
p-1 的尝试，费用正好是 x。"进程重启"是用同一个存储新建用例。spec 一个 WHEN 里用"；"隔开的几种情况
各自从 GIVEN 开始，测试里各用一个新的用例。

「已经开始的案件」「子 agent 任务的费用」的场景和已有的任务派发、备用模型的用例一起驱动，三者共用
一个时钟；调用方（M5）的做法写在测试里：每次拿到用量的尝试记下费用，任务的各条费用加起来交给任务
派发。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tests.fakes.clock import FakeClock
from tests.fakes.costs import InMemoryCostStore
from tests.modules.gateway.builders import (
    C1,
    K1,
    K2,
    K3,
    K5,
    K6,
    K7,
    K8,
    NOW,
    P1,
    PRICE_REQUEST,
    S1,
    S2,
    SIGNAL,
    SIGNAL_JSON,
    T1,
    at,
    budget_service,
    fallback_service,
    request,
    spend,
    task_service,
    tokens,
)
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.model import ModelUse, TokenUsage
from tradingagentspro.contracts.task import Dispatcher, ResultStatus, Task, TaskRequest
from tradingagentspro.modules.gateway import (
    AllUnavailable,
    Attempt,
    Budget,
    BudgetStatus,
    CallBudgetService,
    CallCost,
    CallError,
    CaseScope,
    DeadlineReached,
    DispatchAction,
    ModelCall,
)

GENERAL, RESERVE, EXPLORATION = Budget.GENERAL, Budget.RESERVE, Budget.EXPLORATION
PM, QUANT, CRITIC = Agent.PM, Agent.QUANT, Agent.CRITIC
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)  # 早于一切记录：since(EPOCH) 是全部记录
TODAY = date(2026, 10, 14)


class _Setup:
    """一个调用预算用例和它的时钟、存储，以及共用这个时钟的任务派发和备用模型的用例。"""

    def __init__(self, **config: Any) -> None:
        self.clock = FakeClock(NOW)
        self.store = InMemoryCostStore()
        self.config = config
        self.service = budget_service(self.clock, self.store, **config)
        self.tasks = task_service(self.clock)
        self.models = fallback_service(self.clock)

    def at(self, moment: datetime) -> "_Setup":
        self.clock.set(moment)
        return self

    def spend(self, case: CaseScope, x: str) -> CallCost:
        """spec 里的"K-n 记下 x"。"""
        return spend(self.service, case, x)

    def status(self) -> BudgetStatus:
        """spec 里的"取得预算的情况"。"""
        return self.service.status()

    def spent(self, budget: Budget) -> Decimal:
        """spec 里的"某个预算今天的费用"。"""
        return self.status().spent[budget]

    def restart(self) -> CallBudgetService:
        """spec 里的"进程重启"：用同一个存储和同样的配置新建用例。"""
        self.service = budget_service(self.clock, self.store, **self.config)
        return self.service

    def entries(self) -> tuple[CallCost, ...]:
        """存储里的全部记录，按写入的先后。"""
        return self.store.since(EPOCH)

    def dispatch(self, case: CaseScope, base: TaskRequest) -> Task:
        """spec 里的"PM 在 K-n 派发某个请求"：派发出去，不被拒绝，返回新的 Task。"""
        (dispatch,) = self.tasks.dispatch(case, Dispatcher.PM, (base,))
        assert dispatch.action is DispatchAction.DISPATCHED
        return dispatch.task

    def attempt_cost(self, case: CaseScope, call: ModelCall, usage: TokenUsage) -> CallCost:
        """调用方在正在进行的尝试拿到用量以后记下费用：模型是这次尝试的模型。"""
        return self.service.record(case, call.agent, call.model, usage)


def _given_general_exhausted() -> _Setup:
    """GIVEN 里的"K-1 记下 2.00"：普通预算正好用完。"""
    setup = _Setup()
    setup.spend(K1, "2.00")
    return setup


# 费用的记录


def test_record_one_attempt() -> None:
    """spec「记下一次尝试」：K-1 的 PM 用 p-1，输入 20,000、输出 2,000，记下一条。"""
    setup = _Setup()

    entry = setup.service.record(K1, PM, P1, tokens(20_000, 2_000))

    assert entry == CallCost(
        case_id=K1.case_id,
        case_type=CaseType.WATCH_EVENT,
        agent=PM,
        model=P1,
        usage=TokenUsage(input_tokens=20_000, output_tokens=2_000),
        cost=Decimal("0.06"),
        budget=GENERAL,
        recorded_at=datetime(2026, 10, 14, 15, tzinfo=UTC),
    )
    assert setup.entries() == (entry,)


def test_each_attempt_is_recorded() -> None:
    """spec「每次尝试各记一条」：K-3 的 Quant 用 s-1，输入 8,000、没有输出，接着用 s-2，输入 8,000、
    输出 1,000。
    """
    setup = _Setup()

    first = setup.service.record(K3, QUANT, S1, tokens(8_000))
    second = setup.service.record(K3, QUANT, S2, tokens(8_000, 1_000))

    assert setup.entries() == (first, second)
    assert [(e.model, e.cost) for e in (first, second)] == [
        (S1, Decimal("0.0024")),
        (S2, Decimal("0.0049")),
    ]
    assert {(e.case_id, e.agent, e.budget) for e in (first, second)} == {
        (K3.case_id, QUANT, GENERAL)
    }
    assert setup.spent(GENERAL) == Decimal("0.0073")


def test_restart_continues_from_the_store() -> None:
    """spec「重启以后接着算」：K-1 记下 2.00，进程重启，用同一个存储重新开始。"""
    setup = _Setup()
    setup.spend(K1, "2.00")
    before = setup.status()

    restarted = setup.restart().status()

    assert restarted == before
    assert restarted.spent[GENERAL] == Decimal("2.00")
    assert GENERAL in restarted.exhausted


# 费用的算法


@pytest.mark.parametrize(
    "fields",
    [{"input_tokens": -1, "output_tokens": 0}, {"input_tokens": 0, "output_tokens": 1.5}],
    ids=["输入 -1", "输出 1.5"],
)
def test_invalid_usage_is_not_recorded(fields: dict[str, Any]) -> None:
    """spec「用量不合法」：用量的输入是 -1；输出是 1.5。都报错，不记下。"""
    setup = _Setup()

    with pytest.raises(ValidationError):
        setup.service.record(K1, PM, P1, TokenUsage.model_validate(fields))

    assert setup.entries() == ()


# 费用记到哪个预算


@pytest.mark.parametrize("case", [K1, K3, K5, K6, K8], ids=["K-1", "K-3", "K-5", "K-6", "K-8"])
def test_general_budget_not_exhausted(case: CaseScope) -> None:
    """spec「普通预算没用完时」：K-1、K-3、K-5、K-6、K-8 各记下 0.10，都记普通预算。"""
    setup = _Setup()

    assert setup.spend(case, "0.10").budget is GENERAL
    assert setup.spent(GENERAL) == Decimal("0.10")


def test_exploration_only_charges_the_exploration_budget() -> None:
    """spec「寻股只记寻股预算」：K-2 记下 0.30 记寻股预算；普通预算用完以后也一样。"""
    setup = _Setup()

    assert setup.spend(K2, "0.30").budget is EXPLORATION
    assert setup.spent(GENERAL) == 0

    setup = _given_general_exhausted()

    assert setup.spend(K2, "0.30").budget is EXPLORATION
    assert setup.spent(GENERAL) == Decimal("2.00")


def test_the_crossing_attempt_charges_the_general_budget() -> None:
    """spec「跨过额度的一条整笔记普通预算」：K-1 记下 1.95；K-3 记下 0.10 记普通预算，接着 K-3 记下
    0.10 记持仓保留预算。
    """
    setup = _Setup()
    setup.spend(K1, "1.95")

    assert setup.spend(K3, "0.10").budget is GENERAL
    status = setup.status()
    assert status.spent[GENERAL] == Decimal("2.05")
    assert GENERAL in status.exhausted
    assert status.spent[RESERVE] == 0

    assert setup.spend(K3, "0.10").budget is RESERVE
    assert setup.spent(RESERVE) == Decimal("0.10")
    assert setup.spent(GENERAL) == Decimal("2.05")


@pytest.mark.parametrize("case", [K1, K5, K7, K8], ids=["K-1", "K-5", "K-7", "K-8"])
def test_other_cases_keep_charging_the_general_budget(case: CaseScope) -> None:
    """spec「普通预算用完以后非持仓案件照常记普通预算」：K-1 记下 2.00 以后，K-1、K-5、K-7、K-8
    各记下 0.10。
    """
    setup = _given_general_exhausted()

    assert setup.spend(case, "0.10").budget is GENERAL
    assert setup.spent(GENERAL) == Decimal("2.10")
    assert setup.spent(RESERVE) == 0


def test_follow_ups_charge_by_case_type() -> None:
    """spec「跟进案件按案件类型」：K-1 记下 2.00 以后，K-6（持仓跟进）记持仓保留预算，K-7（关注
    跟进）记普通预算。
    """
    assert _given_general_exhausted().spend(K6, "0.10").budget is RESERVE
    assert _given_general_exhausted().spend(K7, "0.10").budget is GENERAL


@pytest.mark.parametrize(
    ("agent", "model", "usage"),
    [(QUANT, S1, tokens(8_000, 1_000)), (CRITIC, C1, tokens(30_000, 3_000))],
    ids=["K-3 的 Quant 用 s-1", "K-3 的 Critic 用 c-1"],
)
def test_every_agent_charges_by_case_type(agent: Agent, model: Any, usage: TokenUsage) -> None:
    """spec「案件里各个 agent 都按案件类型」：K-1 记下 2.00 以后，K-3 的 Quant 和 Critic 的尝试都记
    持仓保留预算。
    """
    setup = _given_general_exhausted()

    assert setup.service.record(K3, agent, model, usage).budget is RESERVE


# 每日的划分


def test_the_day_starts_at_the_previous_close() -> None:
    """spec「交易日从上一个交易日收盘开始」：2026-10-13 19:59:59Z K-1 记下 1.00；20:00:00Z K-1 记下
    0.50。
    """
    setup = _Setup()
    setup.at(datetime(2026, 10, 13, 19, 59, 59, tzinfo=UTC)).spend(K1, "1.00")
    setup.at(datetime(2026, 10, 13, 20, tzinfo=UTC)).spend(K1, "0.50")

    status = setup.at(NOW).status()

    assert status.day == TODAY
    assert status.spent[GENERAL] == Decimal("0.50")


def test_budgets_recover_at_the_close() -> None:
    """spec「收盘时恢复」：K-1 记下 2.00；K-3 记下 1.00。19:59:59Z 普通预算和持仓保留预算都已经
    用完，20:00:00Z 是 2026-10-15，三个预算都是 0。
    """
    setup = _Setup()
    setup.spend(K1, "2.00")
    setup.spend(K3, "1.00")

    before = setup.at(datetime(2026, 10, 14, 19, 59, 59, tzinfo=UTC)).status()
    after = setup.at(datetime(2026, 10, 14, 20, tzinfo=UTC)).status()

    assert before.day == TODAY
    assert before.exhausted == {GENERAL, RESERVE}
    assert after == BudgetStatus(
        date(2026, 10, 15),
        {GENERAL: Decimal(0), RESERVE: Decimal(0), EXPLORATION: Decimal(0)},
        frozenset(),
    )


def test_weekend_belongs_to_the_next_session() -> None:
    """spec「周末并进下一个交易日」：周五收盘以后、周六、周日各记下 0.50，周一取得。"""
    setup = _Setup()
    setup.at(datetime(2026, 10, 16, 20, 30, tzinfo=UTC)).spend(K1, "0.50")
    setup.at(datetime(2026, 10, 17, 15, tzinfo=UTC)).spend(K1, "0.50")
    setup.at(datetime(2026, 10, 18, 15, tzinfo=UTC)).spend(K5, "0.50")

    status = setup.at(datetime(2026, 10, 19, 15, tzinfo=UTC)).status()

    assert status.day == date(2026, 10, 19)
    assert status.spent[GENERAL] == Decimal("1.50")


def test_holiday_belongs_to_the_next_session() -> None:
    """spec「休市日并进下一个交易日」：感恩节 K-1 记下 2.00；次日 17:59:59Z 仍已经用完，18:00:00Z
    （提前收盘的收盘时刻）是 2026-11-30，普通预算是 0。
    """
    setup = _Setup()
    setup.at(datetime(2026, 11, 26, 15, tzinfo=UTC)).spend(K1, "2.00")

    before = setup.at(datetime(2026, 11, 27, 17, 59, 59, tzinfo=UTC)).status()
    after = setup.at(datetime(2026, 11, 27, 18, tzinfo=UTC)).status()

    assert before.day == date(2026, 11, 27)
    assert GENERAL in before.exhausted
    assert after.day == date(2026, 11, 30)
    assert after.spent[GENERAL] == 0


def test_close_after_daylight_saving_ends() -> None:
    """spec「夏令时结束以后按新的收盘时刻」：2026-11-02 20:30Z K-1 记下 2.00；20:59:59Z 仍已经用完，
    21:00:00Z 是 2026-11-03。
    """
    setup = _Setup()
    setup.at(at(20, 30, day=2, month=11)).spend(K1, "2.00")

    before = setup.at(datetime(2026, 11, 2, 20, 59, 59, tzinfo=UTC)).status()
    after = setup.at(datetime(2026, 11, 2, 21, tzinfo=UTC)).status()

    assert before.day == date(2026, 11, 2)
    assert GENERAL in before.exhausted
    assert after.day == date(2026, 11, 3)
    assert after.spent[GENERAL] == 0


# 预算用完


def test_no_records() -> None:
    """spec「没有记录」：交易日 2026-10-14；三个预算今天的费用都是 0，都没用完。"""
    assert _Setup().status() == BudgetStatus(
        TODAY,
        {GENERAL: Decimal(0), RESERVE: Decimal(0), EXPLORATION: Decimal(0)},
        frozenset(),
    )


def test_general_budget_boundary() -> None:
    """spec「普通预算的边界」：K-1 记下 1.99 没用完，接着 K-1 记下 0.01 用完。"""
    setup = _Setup()
    setup.spend(K1, "1.99")

    assert setup.status().exhausted == frozenset()

    setup.spend(K1, "0.01")
    status = setup.status()

    assert status.spent[GENERAL] == Decimal("2.00")
    assert status.exhausted == {GENERAL}


def test_exploration_budget_boundary() -> None:
    """spec「寻股预算的边界」：K-2 记下 0.49 没用完，接着 K-2 记下 0.01 用完。"""
    setup = _Setup()
    setup.spend(K2, "0.49")

    assert setup.status().exhausted == frozenset()

    setup.spend(K2, "0.01")

    assert setup.status().exhausted == {EXPLORATION}


def test_reserve_budget_boundary() -> None:
    """spec「持仓保留预算的边界」：K-1 记下 2.00；K-3 记下 0.99 没用完，接着 K-3 记下 0.01 用完。"""
    setup = _given_general_exhausted()
    setup.spend(K3, "0.99")

    assert setup.status().exhausted == {GENERAL}

    setup.spend(K3, "0.01")
    status = setup.status()

    assert status.spent[RESERVE] == Decimal("1.00")
    assert status.exhausted == {GENERAL, RESERVE}


# 预算的配置


def test_other_limits() -> None:
    """spec「改成别的值」：普通预算的额度 1.00 时 K-1 记下 1.00 就用完，接着 K-3 记持仓保留预算；
    持仓保留预算 0.10、寻股预算 0.20 时 K-1 记下 2.00，K-3 记下 0.10，K-2 记下 0.20，三个都用完。
    """
    setup = _Setup(general_budget=Decimal("1.00"))
    setup.spend(K1, "1.00")

    assert GENERAL in setup.status().exhausted
    assert setup.spend(K3, "0.10").budget is RESERVE

    setup = _Setup(reserve_budget=Decimal("0.10"), exploration_budget=Decimal("0.20"))
    setup.spend(K1, "2.00")
    setup.spend(K3, "0.10")
    setup.spend(K2, "0.20")

    assert setup.status().exhausted == set(Budget)


# 已经开始的案件


def _attempt(step: object) -> Attempt:
    assert isinstance(step, Attempt)
    return step


def _total(*entries: CallCost) -> Decimal:
    """调用方算出的子 agent 任务的费用：这个任务各次尝试记下的费用之和。"""
    return sum((entry.cost for entry in entries), Decimal(0))


def test_running_case_dispatches_after_general_budget_is_exhausted() -> None:
    """spec「普通预算用完以后运行中的案件照常派发」：K-1 记下 2.00 以后，PM 在 K-1 派发价格请求，
    派发 T-1；接着 K-1 的 Quant 为 T-1 用 s-1，输入 8,000、输出 1,000，记普通预算。
    """
    setup = _given_general_exhausted()

    t1 = setup.dispatch(K1, PRICE_REQUEST)
    step = _attempt(setup.models.begin(K1.case_id, Agent(t1.target), t1.deadline))
    entry = setup.attempt_cost(K1, step.call, tokens(8_000, 1_000))

    assert t1.task_id == T1
    assert (step.call.agent, step.call.model) == (QUANT, S1)
    assert entry.budget is GENERAL
    assert setup.spent(GENERAL) == Decimal("2.0049")


def test_exploration_runs_after_its_budget_is_exhausted() -> None:
    """spec「寻股预算用完以后寻股照常运行」：K-2 记下 0.50 以后，PM 在 K-2 派发 DDD 的价格请求，
    派发；接着 K-2 记下 0.30，记寻股预算。
    """
    setup = _Setup()
    setup.spend(K2, "0.50")
    assert EXPLORATION in setup.status().exhausted

    setup.dispatch(K2, request(PRICE_REQUEST, symbol="DDD"))
    entry = setup.spend(K2, "0.30")

    assert entry.budget is EXPLORATION
    assert setup.spent(EXPLORATION) == Decimal("0.80")


def test_reserve_budget_keeps_recording_after_it_is_exhausted() -> None:
    """spec「持仓保留预算用完以后照常记」：K-1 记下 2.00；K-3 记下 1.00。PM 在 K-3 派发 CCC 的价格
    请求，派发；接着 K-3 记下 0.10，记持仓保留预算。
    """
    setup = _given_general_exhausted()
    setup.spend(K3, "1.00")
    assert setup.status().exhausted == {GENERAL, RESERVE}

    setup.dispatch(K3, request(PRICE_REQUEST, symbol="CCC"))
    entry = setup.spend(K3, "0.10")

    assert entry.budget is RESERVE
    assert setup.spent(RESERVE) == Decimal("1.10")


def test_exhausted_budgets_do_not_switch_models() -> None:
    """spec「预算用完不切换模型」：三个预算都用完以后 PM 在 K-3 调用，先试 p-1，p-1 没有记下
    不可用。
    """
    setup = _given_general_exhausted()
    setup.spend(K3, "1.00")
    setup.spend(K2, "0.50")
    assert setup.status().exhausted == set(Budget)

    step = _attempt(setup.models.begin(K3.case_id, PM))

    assert (step.call.model, step.call.index, step.call.attempt) == (P1, 0, 1)
    assert step.call.unavailable == ()
    assert step.switches == ()
    assert setup.models.cooling() == {}
    assert setup.models.succeed(step.call) == ModelUse(model=P1, fallback=False)


# 子 agent 任务的费用


def test_retry_and_fallback_costs_are_summed() -> None:
    """spec「重试和备用模型的费用相加」：T-1 的调用里 s-1 第一次超时，没有用量，不记；第二次失败
    （其他调用错误），记下 0.0024；换到 s-2 拿到输出，记下 0.0049。T-1 完成，费用 0.0073。
    """
    setup = _Setup()
    t1 = setup.dispatch(K1, PRICE_REQUEST)
    first = _attempt(setup.models.begin(K1.case_id, Agent(t1.target), t1.deadline))

    retry = _attempt(setup.models.fail(first.call, CallError.TIMEOUT))
    failed = setup.attempt_cost(K1, retry.call, tokens(8_000))
    fallback = _attempt(setup.models.fail(retry.call, CallError.OTHER))
    succeeded = setup.attempt_cost(K1, fallback.call, tokens(8_000, 1_000))
    use = setup.models.succeed(fallback.call)
    result = setup.tasks.receive(T1, SIGNAL_JSON, _total(failed, succeeded), use)

    assert [
        (retry.call.model, retry.call.attempt),
        (fallback.call.model, fallback.call.attempt),
    ] == [
        (S1, 2),
        (S2, 1),
    ]
    assert [(e.model, e.cost) for e in setup.entries()] == [
        (S1, Decimal("0.0024")),
        (S2, Decimal("0.0049")),
    ]
    assert (result.status, result.output, result.model_use) == (
        ResultStatus.COMPLETED,
        SIGNAL,
        ModelUse(model=S2, fallback=True),
    )
    assert result.cost == Decimal("0.0073")


def test_timed_out_task_costs_its_recorded_attempts() -> None:
    """spec「超时和调用失败的任务」：s-1 的第一次尝试失败（其他调用错误），记下 0.0024，之后到了
    截止时间停止尝试，记超时，费用 0.0024。
    """
    setup = _Setup()
    t1 = setup.dispatch(K1, PRICE_REQUEST)
    first = _attempt(setup.models.begin(K1.case_id, Agent(t1.target), t1.deadline))
    failed = setup.attempt_cost(K1, first.call, tokens(8_000))

    stopped = setup.at(t1.deadline).models.fail(first.call, CallError.OTHER)
    result = setup.tasks.expire(T1, _total(failed))

    assert isinstance(stopped, DeadlineReached)
    assert result.status is ResultStatus.TIMED_OUT
    assert result.cost == Decimal("0.0024")


def test_failed_task_without_usage_costs_nothing() -> None:
    """spec「超时和调用失败的任务」：s-1 和 s-2 的尝试都连接失败，没有用量，不记；调用方给出调用
    失败，费用 0。
    """
    setup = _Setup()
    t1 = setup.dispatch(K1, PRICE_REQUEST)
    step: object = setup.models.begin(K1.case_id, Agent(t1.target), t1.deadline)
    while isinstance(step, Attempt):
        step = setup.models.fail(step.call, CallError.CONNECTION)

    assert isinstance(step, AllUnavailable)
    result = setup.tasks.fail(T1, step.detail, _total(*setup.entries()))

    assert setup.entries() == ()
    assert result.status is ResultStatus.FAILED
    assert result.cost == 0
