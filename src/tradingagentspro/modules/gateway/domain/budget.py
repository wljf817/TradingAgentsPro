"""调用预算：三个预算、一次尝试的费用记录和预算的情况，以及费用的算法、记到哪个预算、算出预算的
情况三条规则，见 call-budget spec「费用的记录」「费用的算法」「费用记到哪个预算」「预算用完」。

"今天"的记录由调用方按时刻归属的交易日取出（spec「每日的划分」）；这里不读时钟、不查日历。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.modules.gateway.config import GatewayConfig, ModelPrice


class Budget(StrEnum):
    """三个预算，见 call-budget spec「费用记到哪个预算」。"""

    GENERAL = "general"  # 普通预算
    RESERVE = "reserve"  # 持仓保留预算：普通预算用完以后持仓的案件用
    EXPLORATION = "exploration"  # 寻股预算


# 针对持仓状态的案件类型：普通预算用完以后改用持仓保留预算。和调度器 queue.py 里的同名集合内容
# 相同：模块之间不能 import，各自一份，测试各自和 spec 的表比较
_HOLDING_CASES: Final = frozenset(
    {
        CaseType.HOLDING_RISK_EVENT,
        CaseType.HOLDING_EVENT,
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.HOLDING_FOLLOW_UP,
    }
)
_MILLION: Final = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class CallCost:
    """一次尝试的费用记录，写入以后不改。"""

    case_id: UUID
    case_type: CaseType  # 案件开始运行时确定的类型
    agent: Agent
    model: ModelRef
    usage: TokenUsage
    cost: Decimal  # 美元
    budget: Budget
    recorded_at: datetime  # 记下的时刻


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """现在归属的交易日里三个预算的情况。"""

    day: date  # 现在归属的交易日
    spent: Mapping[Budget, Decimal]  # 每个预算今天的费用，没有记录的是 0
    exhausted: frozenset[Budget]  # 今天用完的预算


def cost_of(usage: TokenUsage, price: ModelPrice) -> Decimal:
    """一次尝试的费用：用量乘每百万 token 的单价，精确计算、不舍入。"""
    spent = usage.input_tokens * price.input_price + usage.output_tokens * price.output_price
    return spent / _MILLION


def charged_budget(case_type: CaseType, general_exhausted: bool) -> Budget:
    """这一条记到哪个预算；general_exhausted 是记这一条以前普通预算是否已经用完。"""
    if case_type is CaseType.EXPLORATION:
        return Budget.EXPLORATION
    if general_exhausted and case_type in _HOLDING_CASES:
        return Budget.RESERVE
    return Budget.GENERAL


def budget_status(costs: Iterable[CallCost], day: date, config: GatewayConfig) -> BudgetStatus:
    """day 是现在归属的交易日，costs 是记下的时刻归属这一天的记录。

    一个预算今天的费用不小于它的额度就用完，三个预算各自判断。
    """
    spent = dict.fromkeys(Budget, Decimal(0))
    for entry in costs:
        spent[entry.budget] += entry.cost
    limits = {
        Budget.GENERAL: config.general_budget,
        Budget.RESERVE: config.reserve_budget,
        Budget.EXPLORATION: config.exploration_budget,
    }
    exhausted = frozenset(budget for budget in Budget if spent[budget] >= limits[budget])
    return BudgetStatus(day, spent, exhausted)
