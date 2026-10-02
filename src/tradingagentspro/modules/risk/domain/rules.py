"""风控规则：开仓类和减仓类各一组，规则表见 risk-rules spec。

新增规则：写一个满足 RiskRule 的函数，加进对应的一组，不修改已有规则。
"""

from decimal import Decimal
from typing import Protocol

from tradingagentspro.contracts.risk import RiskViolation
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk.domain.exposure import (
    ZERO,
    effective_emergency_threshold,
    execution_day,
    holding_of,
    holding_risk,
    merged_risk,
    position_value,
    sector_value,
    sellable_quantity,
)
from tradingagentspro.modules.risk.domain.request import RiskRequest


class RiskRule(Protocol):
    """一条风控规则：按 quantity 股执行能通过时返回 None，否则返回违规。

    必须满足单调性：某个股数能通过，任何更小的正整数股数也能通过。判定靠这个性质
    找出能通过的最大股数。
    """

    def __call__(self, request: RiskRequest, quantity: int) -> RiskViolation | None: ...


def _amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def _prices(plan: TradePlan) -> tuple[Decimal, Decimal]:
    """开仓方案的限价和止损价。"""
    assert plan.limit_price is not None  # 方案格式保证开仓方案有限价和止损价
    assert plan.stop_price is not None
    return plan.limit_price, plan.stop_price


def _merged_risk(request: RiskRequest, quantity: int) -> Decimal:
    plan = request.plan
    limit_price, stop_price = _prices(plan)
    holding = holding_of(request.account, plan.symbol)
    return merged_risk(holding, quantity, limit_price, stop_price, request.config.cost_buffer_ratio)


# 开仓类规则


def settled_cash(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """股数乘以限价乘以（1 + 缓冲比例），不超过已结算资金减去开仓预留的资金。"""
    limit_price, _ = _prices(request.plan)
    cost = quantity * limit_price * (1 + request.config.cost_buffer_ratio)
    reserved = sum((r.cash for r in request.opening_reservations), ZERO)
    available = request.account.settled_cash - reserved
    if cost <= available:
        return None
    return RiskViolation(
        rule="已结算资金",
        message=f"需要 {_amount(cost)}，扣除预留后的已结算资金只有 {_amount(available)}",
    )


def position_limit(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """这只股票的持仓市值加上买入部分的市值，不超过权益乘以单只仓位比例。"""
    plan, account = request.plan, request.account
    limit_price, _ = _prices(plan)
    value = position_value(holding_of(account, plan.symbol), quantity, limit_price)
    cap = account.equity * request.config.max_position_ratio
    if value <= cap:
        return None
    return RiskViolation(
        rule="单只仓位上限",
        message=f"{plan.symbol} 的市值将是 {_amount(value)}，超过上限 {_amount(cap)}",
    )


def trade_risk(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """这只股票合并后的计划风险，不超过权益乘以单笔风险比例。"""
    risk = _merged_risk(request, quantity)
    cap = request.account.equity * request.config.max_trade_risk_ratio
    if risk <= cap:
        return None
    return RiskViolation(
        rule="单笔计划风险",
        message=f"{request.plan.symbol} 的计划风险将是 {_amount(risk)}，超过上限 {_amount(cap)}",
    )


def total_risk(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """其他持仓的计划风险、这只股票合并后的计划风险和开仓预留的风险之和，
    不超过权益乘以合计风险比例。
    """
    plan, account, config = request.plan, request.account, request.config
    others = sum(
        (
            holding_risk(p, config.cost_buffer_ratio)
            for p in account.positions
            if p.symbol != plan.symbol
        ),
        ZERO,
    )
    reserved = sum((r.risk for r in request.opening_reservations), ZERO)
    total = others + _merged_risk(request, quantity) + reserved
    cap = account.equity * config.max_total_risk_ratio
    if total <= cap:
        return None
    return RiskViolation(
        rule="组合合计风险",
        message=f"组合合计风险将是 {_amount(total)}，超过上限 {_amount(cap)}",
    )


def sector_limit(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """同板块的持仓市值、开仓预留的市值和买入部分的市值之和，不超过权益乘以板块比例。"""
    plan, account = request.plan, request.account
    limit_price, _ = _prices(plan)
    value = sector_value(account, request.opening_reservations, plan.sector)
    value += quantity * limit_price
    cap = account.equity * request.config.max_sector_ratio
    if value <= cap:
        return None
    return RiskViolation(
        rule="板块集中度",
        message=f"{plan.sector}板块的市值将是 {_amount(value)}，超过上限 {_amount(cap)}",
    )


def max_positions(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """只对没有持仓的股票检查：持仓的股票、有开仓预留的股票和这只股票去重后，
    不超过最多持仓数。
    """
    plan, config = request.plan, request.config
    held = {p.symbol for p in request.account.positions}
    if plan.symbol in held:
        return None
    symbols = held | {r.symbol for r in request.opening_reservations} | {plan.symbol}
    if len(symbols) <= config.max_positions:
        return None
    return RiskViolation(
        rule="最多持仓数",
        message=(
            f"新建仓后将有 {len(symbols)} 只股票（含有开仓预留的），超过上限 {config.max_positions}"
        ),
    )


def pending_opening(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """这只股票没有开仓预留，也就是没有未完成的开仓方案。"""
    symbol = request.plan.symbol
    if all(r.symbol != symbol for r in request.opening_reservations):
        return None
    return RiskViolation(
        rule="未完成的开仓方案",
        message=f"{symbol} 还有未完成的开仓方案，等它结束后再提",
    )


def trade_interval(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """有最近成交日时：从最近成交日到执行交易日的交易日个数，不少于最短交易间隔。"""
    plan, config = request.plan, request.config
    last = request.account.last_trade_days.get(plan.symbol)
    if last is None:
        return None
    day = execution_day(request.now, request.calendar)
    sessions = request.calendar.count_sessions(last, day)
    if sessions >= config.min_trade_interval_sessions:
        return None
    return RiskViolation(
        rule="最短交易间隔",
        message=(
            f"{plan.symbol} 最近成交日是 {last}，到执行交易日 {day} 只隔 {sessions} 个交易日，"
            f"至少要 {config.min_trade_interval_sessions} 个"
        ),
    )


def stop_below_limit(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """止损价低于限价。"""
    limit_price, stop_price = _prices(request.plan)
    if stop_price < limit_price:
        return None
    return RiskViolation(rule="止损价", message=f"止损价 {stop_price} 必须低于限价 {limit_price}")


def emergency_threshold(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """生效的紧急阈值在下限和上限之间（含两端）；方案没填时用默认值。"""
    config = request.config
    threshold = effective_emergency_threshold(request.plan, config)
    if config.emergency_threshold_min <= threshold <= config.emergency_threshold_max:
        return None
    return RiskViolation(
        rule="紧急阈值",
        message=(
            f"紧急阈值 {threshold} 不在 {config.emergency_threshold_min} "
            f"到 {config.emergency_threshold_max} 之间"
        ),
    )


def daily_loss_breaker(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """今天的单日亏损熔断没有触发。"""
    if not request.daily_loss_breaker_tripped:
        return None
    return RiskViolation(rule="单日亏损熔断", message="今天的单日亏损熔断已经触发，禁止开仓")


OPENING_RULES: tuple[RiskRule, ...] = (
    settled_cash,
    position_limit,
    trade_risk,
    total_risk,
    sector_limit,
    max_positions,
    pending_opening,
    trade_interval,
    stop_below_limit,
    emergency_threshold,
    daily_loss_breaker,
)


# 减仓类规则：不受开仓类规则限制，保证退出不被阻塞。退出方案不带止损价，卖出不超过持仓，
# 账户只做多，所以卖出不会增加风险，不另外检查。


def sellable(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """卖出股数不超过可卖数量：持仓股数减去卖出预留的股数。"""
    symbol = request.plan.symbol
    available = sellable_quantity(request.account, request.sell_reservations, symbol)
    if quantity <= available:
        return None
    return RiskViolation(
        rule="可卖数量", message=f"{symbol} 只有 {available} 股可卖，要卖 {quantity} 股"
    )


EXIT_RULES: tuple[RiskRule, ...] = (sellable,)
