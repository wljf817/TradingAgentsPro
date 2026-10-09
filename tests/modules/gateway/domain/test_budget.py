"""调用预算纯逻辑的测试：费用的算法、记到哪个预算、预算的情况，直接构造用量、单价和费用记录。

规则见 openspec 的 call-budget spec「费用的算法」「费用记到哪个预算」「预算用完」；单价用 builders
里的基准单价（美元/百万 token）。默认额度是普通预算 2.00、持仓保留预算 1.00、寻股预算 0.50。
"""

from datetime import date
from decimal import Decimal

import pytest

from tests.modules.gateway.builders import BASELINE_MODELS, C1, K1, NOW, P1, S1
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.modules.gateway import (
    Budget,
    BudgetStatus,
    CallCost,
    GatewayConfig,
    budget_status,
    charged_budget,
    cost_of,
)

GENERAL, RESERVE, EXPLORATION = Budget.GENERAL, Budget.RESERVE, Budget.EXPLORATION
DAY = date(2026, 10, 14)
NOTHING = {GENERAL: Decimal(0), RESERVE: Decimal(0), EXPLORATION: Decimal(0)}


def _cost(model: ModelRef, input_tokens: int, output_tokens: int) -> Decimal:
    usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    return cost_of(usage, BASELINE_MODELS.price(model))


def _entry(budget: Budget, cost: str) -> CallCost:
    """记到 budget、费用是 cost 的一条记录；案件、agent、模型和用量不影响预算的情况。"""
    return CallCost(
        case_id=K1.case_id,
        case_type=CaseType.WATCH_EVENT,
        agent=Agent.PM,
        model=P1,
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        cost=Decimal(cost),
        budget=budget,
        recorded_at=NOW,
    )


def _status(*entries: CallCost, config: GatewayConfig | None = None) -> BudgetStatus:
    return budget_status(entries, DAY, GatewayConfig() if config is None else config)


# 费用的算法


@pytest.mark.parametrize(
    ("model", "input_tokens", "output_tokens", "expected"),
    [
        (P1, 20_000, 2_000, "0.06"),
        (S1, 8_000, 1_000, "0.0049"),
        (C1, 30_000, 3_000, "0.0675"),
    ],
    ids=["K-1 的 PM 用 p-1", "K-3 的 Quant 用 s-1", "K-3 的 Critic 用 c-1"],
)
def test_cost_by_price(
    model: ModelRef, input_tokens: int, output_tokens: int, expected: str
) -> None:
    """spec「按单价算」：输入 token 数乘输入单价加输出 token 数乘输出单价，再除以一百万。"""
    assert _cost(model, input_tokens, output_tokens) == Decimal(expected)


@pytest.mark.parametrize(
    ("model", "input_tokens", "output_tokens", "expected"),
    [(P1, 1, 0, "0.000002"), (S1, 1, 0, "0.0000003"), (P1, 0, 0, "0")],
    ids=["p-1 输入 1", "s-1 输入 1", "p-1 输入 0、输出 0"],
)
def test_cost_is_not_rounded(
    model: ModelRef, input_tokens: int, output_tokens: int, expected: str
) -> None:
    """spec「不舍入」：一个 token 的费用也精确算出。"""
    assert _cost(model, input_tokens, output_tokens) == Decimal(expected)


def test_large_usage_is_exact() -> None:
    """用量很大、单价带小数时也不丢精度：s-1 输入 123,456,789、输出 98,765,432，费用是
    37.0370367 加 246.91358。
    """
    assert _cost(S1, 123_456_789, 98_765_432) == Decimal("283.9506167")


# 费用记到哪个预算

# spec「费用记到哪个预算」的表：每种案件类型在记之前普通预算没用完、已经用完时记到的预算
CHARGED: dict[CaseType, tuple[Budget, Budget]] = {
    CaseType.EXPLORATION: (EXPLORATION, EXPLORATION),
    CaseType.HOLDING_RISK_EVENT: (GENERAL, RESERVE),
    CaseType.HOLDING_EVENT: (GENERAL, RESERVE),
    CaseType.HOLDING_REVIEW: (GENERAL, RESERVE),
    CaseType.HOLDING_CATALYST_REVIEW: (GENERAL, RESERVE),
    CaseType.HOLDING_FOLLOW_UP: (GENERAL, RESERVE),
    CaseType.WATCH_EVENT: (GENERAL, GENERAL),
    CaseType.WATCH_REVIEW: (GENERAL, GENERAL),
    CaseType.WATCH_CATALYST_REVIEW: (GENERAL, GENERAL),
    CaseType.WATCH_FOLLOW_UP: (GENERAL, GENERAL),
    CaseType.CANDIDATE_EVENT: (GENERAL, GENERAL),
    CaseType.CANDIDATE_EVALUATION: (GENERAL, GENERAL),
    CaseType.WEEKLY_RETROSPECTIVE: (GENERAL, GENERAL),
    CaseType.SELL_RETROSPECTIVE: (GENERAL, GENERAL),
}


def test_the_table_covers_every_case_type() -> None:
    """表里的案件类型正好是 14 种。"""
    assert set(CHARGED) == set(CaseType)


@pytest.mark.parametrize("case_type", list(CaseType))
def test_charged_budget_follows_the_table(case_type: CaseType) -> None:
    """spec「费用记到哪个预算」的表：逐个案件类型检查普通预算没用完和已经用完两种情况。"""
    before, after = CHARGED[case_type]

    assert charged_budget(case_type, general_exhausted=False) is before
    assert charged_budget(case_type, general_exhausted=True) is after


# 预算的情况


def test_no_costs() -> None:
    """spec「没有记录」：三个预算今天的费用都是 0，都没用完；交易日原样给出。"""
    status = _status()

    assert status == BudgetStatus(DAY, NOTHING, frozenset())
    assert budget_status((), date(2026, 11, 30), GatewayConfig()).day == date(2026, 11, 30)


def test_costs_are_summed_per_budget() -> None:
    """每个预算今天的费用是记到这个预算的记录的费用之和。"""
    status = _status(
        _entry(GENERAL, "0.06"),
        _entry(EXPLORATION, "0.30"),
        _entry(GENERAL, "0.0049"),
        _entry(RESERVE, "0.10"),
        _entry(GENERAL, "0.0675"),
    )

    assert status.spent == {
        GENERAL: Decimal("0.1324"),
        RESERVE: Decimal("0.10"),
        EXPLORATION: Decimal("0.30"),
    }
    assert status.exhausted == frozenset()


@pytest.mark.parametrize(
    ("budget", "below", "limit"),
    [(GENERAL, "1.99", "2.00"), (RESERVE, "0.99", "1.00"), (EXPLORATION, "0.49", "0.50")],
    ids=["普通预算", "持仓保留预算", "寻股预算"],
)
def test_budget_boundary(budget: Budget, below: str, limit: str) -> None:
    """spec「普通预算的边界」「寻股预算的边界」「持仓保留预算的边界」：额度减 0.01 没用完，正好
    等于额度用完；其他两个预算不受影响。
    """
    assert _status(_entry(budget, below)).exhausted == frozenset()
    assert _status(_entry(budget, below), _entry(budget, "0.01")).exhausted == {budget}
    assert _status(_entry(budget, limit)).spent[budget] == Decimal(limit)


@pytest.mark.parametrize("budget", list(Budget))
def test_over_the_limit_is_still_exhausted(budget: Budget) -> None:
    """超出额度仍算用完：已经开始的案件可以让预算超出（spec「已经开始的案件」）。"""
    status = _status(_entry(budget, "5.00"))

    assert status.exhausted == {budget}
    assert status.spent[budget] == Decimal("5.00")


def test_budgets_are_independent() -> None:
    """spec「预算用完」：三个预算各自判断，互不影响。"""
    assert _status(_entry(GENERAL, "2.00"), _entry(RESERVE, "0.99")).exhausted == {GENERAL}
    assert _status(_entry(RESERVE, "1.00"), _entry(GENERAL, "1.99")).exhausted == {RESERVE}
    assert _status(_entry(EXPLORATION, "0.50"), _entry(GENERAL, "1.99")).exhausted == {EXPLORATION}
    assert _status(
        _entry(GENERAL, "2.00"), _entry(RESERVE, "1.00"), _entry(EXPLORATION, "0.50")
    ).exhausted == set(Budget)


def test_smaller_limits() -> None:
    """用较小的额度也成立：普通预算 0.50、持仓保留预算 0.20、寻股预算 0.10。"""
    config = GatewayConfig(
        general_budget=Decimal("0.50"),
        reserve_budget=Decimal("0.20"),
        exploration_budget=Decimal("0.10"),
    )

    below = _status(
        _entry(GENERAL, "0.49"),
        _entry(RESERVE, "0.19"),
        _entry(EXPLORATION, "0.09"),
        config=config,
    )
    at_limit = _status(
        _entry(GENERAL, "0.50"),
        _entry(RESERVE, "0.20"),
        _entry(EXPLORATION, "0.10"),
        config=config,
    )

    assert below.exhausted == frozenset()
    assert at_limit.exhausted == set(Budget)
