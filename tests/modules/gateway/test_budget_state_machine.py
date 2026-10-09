"""调用预算的状态机性质测试（add-call-budget 的 design D13）。

随机交错记下费用、拨动时间和进程重启：
- 记下：随机的案件类型（14 种）、agent、模型和用量，费用从 0 到几十美分；或者用 p-1 记下几美分到
  几十美分的整数美分；或者补到这个案件记到的预算的额度，让额度正好被碰到
- 拨动时间：往后拨几分钟到几天，或者拨到收盘前后一秒，跨收盘、跨周末和休市日，不晚于 2027-01-28；
  开始的时刻取基准时刻，或者几个靠近周末、夏令时结束和休市日的时刻
- 重启：用同一个存储新建用例
配置用较小的额度：普通预算 0.50、持仓保留预算 0.20、寻股预算 0.10，让额度经常被碰到。

参考模型按 spec 自己记下每条记录：费用按「费用的算法」，记到的预算按「费用记到哪个预算」的表和参考
模型里记之前普通预算是否用完，今天按「每日的划分」取记下的时刻归属的交易日和现在归属的交易日相同的
记录。每一步之后检查：
- 每个预算今天的费用等于参考模型里今天记到这个预算的费用之和；用完当且仅当不小于额度
- 寻股的记录都在寻股预算，寻股预算里只有寻股的记录
- 持仓保留预算里只有持仓的五种案件类型的记录，并且每一条记下以前普通预算已经用完；其他非寻股的
  记录都在普通预算
- 持仓保留预算今天的费用大于 0 时，普通预算已经用完
- 跨过收盘以后、新的记录以前，三个预算今天的费用都是 0
- 重启前后预算的情况相同
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import ceil
from uuid import UUID

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from tests.fakes.clock import FakeClock
from tests.fakes.costs import InMemoryCostStore
from tests.modules.gateway.builders import BASELINE_MODELS, BASELINE_PRICES, NOW, NYSE, P1
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.modules.gateway import (
    Budget,
    BudgetStatus,
    CallBudgetService,
    CallCost,
    CaseScope,
    GatewayConfig,
)

GENERAL, RESERVE, EXPLORATION = Budget.GENERAL, Budget.RESERVE, Budget.EXPLORATION
CONFIG = GatewayConfig(
    general_budget=Decimal("0.50"),
    reserve_budget=Decimal("0.20"),
    exploration_budget=Decimal("0.10"),
)
LIMITS = {
    GENERAL: CONFIG.general_budget,
    RESERVE: CONFIG.reserve_budget,
    EXPLORATION: CONFIG.exploration_budget,
}
NAMES = {GENERAL: "普通预算", RESERVE: "持仓保留预算", EXPLORATION: "寻股预算"}
# spec「费用记到哪个预算」表里持仓的五种案件类型
HOLDING_CASES = frozenset(
    {
        CaseType.HOLDING_RISK_EVENT,
        CaseType.HOLDING_EVENT,
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.HOLDING_FOLLOW_UP,
    }
)
# 基准单价：每个模型的输入单价和输出单价，美元/百万 token
PRICES = {p.model: (p.input_price, p.output_price) for p in BASELINE_PRICES}
MODELS = tuple(PRICES)
MILLION = Decimal(1_000_000)
CENTS = ("0.01", "0.05", "0.10", "0.20")  # 用 p-1 记下的整数美分
P1_TOKEN = PRICES[P1][0] / MILLION  # p-1 一个输入 token 的费用

# 开始的时刻：基准时刻（周三盘中）、周五收盘前、夏令时结束前的周五收盘前、感恩节前一天收盘前、
# 平安夜（提前收盘）和圣诞节前、元旦前、马丁·路德·金纪念日前的周五收盘前
STARTS = (
    NOW,
    datetime(2026, 10, 16, 19, tzinfo=UTC),
    datetime(2026, 10, 30, 19, tzinfo=UTC),
    datetime(2026, 11, 25, 19, tzinfo=UTC),
    datetime(2026, 12, 23, 19, tzinfo=UTC),
    datetime(2026, 12, 31, 19, tzinfo=UTC),
    datetime(2027, 1, 15, 19, tzinfo=UTC),
)
LATEST = datetime(2027, 1, 28, 20, tzinfo=UTC)  # 现在不晚于它，仍在样例日历之内


def _day(moment: datetime) -> date:
    """moment 归属的交易日（trading-calendar spec「时刻归属的交易日」）。"""
    return NYSE.session_closing_after(moment).day


def _scope(case_type: CaseType) -> CaseScope:
    """每种案件类型一个案件。"""
    return CaseScope(UUID(int=0xC000 + list(CaseType).index(case_type)), case_type, None)


@dataclass(frozen=True, slots=True)
class _Expected:
    """参考模型里的一条记录：按 spec 算出的费用、记到的预算，以及记之前普通预算是否已经用完。"""

    case_type: CaseType
    cost: Decimal
    budget: Budget
    recorded_at: datetime
    general_exhausted: bool


class CallBudgetMachine(RuleBasedStateMachine):
    """参考模型只按 spec 写，检查时只读用例的返回、预算的情况和存储里的记录。"""

    def __init__(self) -> None:
        super().__init__()
        self.clock = FakeClock(NOW)
        self.store = InMemoryCostStore()
        self.service = self._new_service()
        self.expected: list[_Expected] = []

    def _new_service(self) -> CallBudgetService:
        return CallBudgetService(self.store, self.clock, NYSE, BASELINE_MODELS, CONFIG)

    @property
    def now(self) -> datetime:
        return self.clock.now()

    # 参考模型

    def _spent(self) -> dict[Budget, Decimal]:
        """每个预算今天的费用：记下的时刻归属的交易日是今天、记到这个预算的费用之和。"""
        today = _day(self.now)
        spent = dict.fromkeys(Budget, Decimal(0))
        for expected in self.expected:
            if _day(expected.recorded_at) == today:
                spent[expected.budget] += expected.cost
        return spent

    def _charged(self, case_type: CaseType) -> tuple[Budget, bool]:
        """spec「费用记到哪个预算」的表：现在记一条会记到的预算，以及记之前普通预算是否已经用完。"""
        general_exhausted = self._spent()[GENERAL] >= LIMITS[GENERAL]
        if case_type is CaseType.EXPLORATION:
            return EXPLORATION, general_exhausted
        if case_type in HOLDING_CASES and general_exhausted:
            return RESERVE, general_exhausted
        return GENERAL, general_exhausted

    def _record(
        self, case_type: CaseType, agent: Agent, model: ModelRef, usage: TokenUsage
    ) -> None:
        input_price, output_price = PRICES[model]
        cost = (
            usage.input_tokens * input_price / MILLION
            + usage.output_tokens * output_price / MILLION
        )
        budget, general_exhausted = self._charged(case_type)
        before = self.service.status().exhausted

        entry = self.service.record(_scope(case_type), agent, model, usage)

        assert entry == CallCost(
            case_id=_scope(case_type).case_id,
            case_type=case_type,
            agent=agent,
            model=model,
            usage=usage,
            cost=cost,
            budget=budget,
            recorded_at=self.now,
        )
        self.expected.append(_Expected(case_type, cost, budget, self.now, general_exhausted))
        for newly in self.service.status().exhausted - before:
            event(f"{NAMES[newly]}用完")
        if budget is RESERVE:
            event("持仓案件记到持仓保留预算")
        if self._spent()[budget] == LIMITS[budget]:
            event("正好花到额度")

    # 记下

    @rule(
        case_type=st.sampled_from(CaseType),
        agent=st.sampled_from(Agent),
        model=st.sampled_from(MODELS),
        input_tokens=st.integers(0, 100_000),
        output_tokens=st.integers(0, 20_000),
    )
    def record(
        self,
        case_type: CaseType,
        agent: Agent,
        model: ModelRef,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """随机的用量：费用从 0 到几十美分，大多不是整数美分。"""
        usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        self._record(case_type, agent, model, usage)

    @rule(case_type=st.sampled_from(CaseType), x=st.sampled_from(CENTS))
    def spend(self, case_type: CaseType, x: str) -> None:
        """spec 里的"K-n 记下 x"：用 p-1 记下整数美分。"""
        usage = TokenUsage(input_tokens=int(Decimal(x) / P1_TOKEN), output_tokens=0)
        self._record(case_type, Agent.PM, P1, usage)

    @rule(case_type=st.sampled_from(CaseType))
    def fill(self, case_type: CaseType) -> None:
        """用 p-1 补到这个案件现在记到的预算的额度：差额是 p-1 一个 token 的整数倍时正好补到，
        否则多出不到一个 token 的钱。已经用完时什么也不做。
        """
        budget, _ = self._charged(case_type)
        remaining = LIMITS[budget] - self._spent()[budget]
        if remaining <= 0:
            return
        usage = TokenUsage(input_tokens=ceil(remaining / P1_TOKEN), output_tokens=0)
        self._record(case_type, Agent.PM, P1, usage)

    # 拨动时间

    @initialize(start=st.sampled_from(STARTS))
    def start_at(self, start: datetime) -> None:
        self.clock.set(start)

    def _move_to(self, target: datetime) -> None:
        """把时钟拨到 target（不晚于 LATEST）。跨过收盘时，新的交易日还没有记录，三个预算都是 0。"""
        before_day, before = _day(self.now), self.service.status()
        self.clock.set(min(target, LATEST))
        after_day = _day(self.now)
        if after_day == before_day:
            return
        status = self.service.status()
        assert status == BudgetStatus(after_day, dict.fromkeys(Budget, Decimal(0)), frozenset())
        if any(before.spent.values()):
            event("跨过收盘恢复")
        # 两个交易日之间不开市的日子：周末和休市日
        between = (before_day + timedelta(days=n) for n in range(1, (after_day - before_day).days))
        closed = [day for day in between if not NYSE.is_session(day)]
        if any(day.weekday() >= 5 for day in closed):
            event("跨过周末")
        if any(day.weekday() < 5 for day in closed):
            event("跨过休市日")

    @rule(minutes=st.integers(1, 3 * 24 * 60))
    def advance(self, minutes: int) -> None:
        """往后拨几分钟到几天。"""
        self._move_to(self.now + timedelta(minutes=minutes))

    @rule(seconds=st.sampled_from([-1, 0, 1]))
    def to_the_close(self, seconds: int) -> None:
        """拨到现在归属的交易日收盘的前一秒、收盘时刻或后一秒，碰每日的划分的边界。"""
        close = NYSE.session_closing_after(self.now).close
        target = close + timedelta(seconds=seconds)
        self._move_to(target if target > self.now else close)

    # 重启

    @rule()
    def restart(self) -> None:
        """进程重启：用同一个存储新建用例，预算的情况不变。"""
        before = self.service.status()
        self.service = self._new_service()
        assert self.service.status() == before
        event("重启")

    # 不变量

    @invariant()
    def status_matches_the_reference(self) -> None:
        """每个预算今天的费用等于参考模型的；用完当且仅当不小于额度；交易日是现在归属的交易日。"""
        status = self.service.status()
        spent = self._spent()
        assert status.day == _day(self.now)
        assert dict(status.spent) == spent
        assert status.exhausted == {b for b in Budget if spent[b] >= LIMITS[b]}

    @invariant()
    def records_match_the_reference(self) -> None:
        """存储里的记录和参考模型一一对应，按写入的先后。"""
        entries = self.store.since(datetime(2000, 1, 1, tzinfo=UTC))
        assert [(e.case_type, e.cost, e.budget, e.recorded_at) for e in entries] == [
            (x.case_type, x.cost, x.budget, x.recorded_at) for x in self.expected
        ]

    @invariant()
    def records_are_in_the_right_budget(self) -> None:
        """寻股只在寻股预算；持仓保留预算里只有记之前普通预算已经用完的持仓案件；其余在普通预算。"""
        for x in self.expected:
            assert (x.case_type is CaseType.EXPLORATION) == (x.budget is EXPLORATION)
            if x.budget is RESERVE:
                assert x.case_type in HOLDING_CASES and x.general_exhausted
            elif x.budget is GENERAL:
                assert not (x.case_type in HOLDING_CASES and x.general_exhausted)

    @invariant()
    def reserve_only_after_general(self) -> None:
        """持仓保留预算今天的费用大于 0 时，普通预算已经用完。"""
        status = self.service.status()
        if status.spent[RESERVE] > 0:
            assert GENERAL in status.exhausted


TestCallBudgetMachine = CallBudgetMachine.TestCase
