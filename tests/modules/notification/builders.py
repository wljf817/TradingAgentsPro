"""通知测试的数据：discord-notification spec 的基准数据和场景里的方案 P1 到 P4。

Discord 适配器的测试也用这里的数据。
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.notification import ConfirmationRequest

USER_ID = 1001  # 配置的用户，场景里叫「本人」
OTHER_USER_ID = 4004  # 「别人」
CHANNEL_ID = 2002
GUILD_ID = 3003

REQUESTED_AT = datetime(2026, 10, 8, 14, tzinfo=UTC)  # 请求确认的时刻
DEADLINE = datetime(2026, 10, 8, 14, 30, tzinfo=UTC)  # 默认确认超时下的截止时刻
EXPIRES_AT = datetime(2026, 10, 8, 20, tzinfo=UTC)  # 风控给出的生效有效期

P1_ID = UUID("0b7c2d4e-3f5a-4b6c-8d9e-0f1a2b3c4d5e")
P2_ID = UUID(int=2)
P3_ID = UUID(int=3)
P4_ID = UUID(int=4)


def p1() -> TradePlan:
    """买入 AAPL 100 股，限价 180.50，止损价 171.00。"""
    return TradePlan(
        plan_id=P1_ID,
        symbol="AAPL",
        sector="科技",
        action=TradeAction.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("180.50"),
        stop_price=Decimal("171.00"),
    )


def p1_decision(outcome: RiskOutcome = RiskOutcome.PASSED) -> RiskDecision:
    """P1 的风控结果：默认通过，100 股，紧急阈值 0.08；拒绝时没有股数、阈值和有效期。"""
    if outcome is RiskOutcome.REJECTED:
        return RiskDecision(
            outcome=outcome,
            quantity=None,
            emergency_threshold=None,
            expires_at=None,
            violations=(RiskViolation(rule="降级模式", message="系统处于降级模式，暂停开仓"),),
        )
    return RiskDecision(
        outcome=outcome,
        quantity=100,
        emergency_threshold=Decimal("0.08"),
        expires_at=EXPIRES_AT,
        violations=(),
    )


def p2() -> TradePlan:
    """加仓 MSFT 200 股，限价 410.00，止损价 395.00，允许价格偏离 0.05。"""
    return TradePlan(
        plan_id=P2_ID,
        symbol="MSFT",
        sector="科技",
        action=TradeAction.ADD,
        quantity=200,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("410.00"),
        stop_price=Decimal("395.00"),
        max_price_deviation=Decimal("0.05"),
    )


def p2_decision() -> RiskDecision:
    """P2 被风控调整为 120 股，紧急阈值 0.075，两条规则不通过。"""
    return RiskDecision(
        outcome=RiskOutcome.ADJUSTED,
        quantity=120,
        emergency_threshold=Decimal("0.075"),
        expires_at=EXPIRES_AT,
        violations=(
            RiskViolation(
                rule="单笔计划风险", message="MSFT 的计划风险将是 3,040.00，超过上限 2,000.00"
            ),
            RiskViolation(
                rule="组合合计风险", message="组合合计风险将是 9,000.00，超过上限 8,000.00"
            ),
        ),
    )


def p3() -> TradePlan:
    """减仓 AAPL 60 股，市价单。"""
    return TradePlan(
        plan_id=P3_ID,
        symbol="AAPL",
        sector="科技",
        action=TradeAction.REDUCE,
        quantity=60,
        order_type=OrderType.MARKET,
    )


def p3_decision() -> RiskDecision:
    return RiskDecision(
        outcome=RiskOutcome.PASSED,
        quantity=60,
        emergency_threshold=None,
        expires_at=EXPIRES_AT,
        violations=(),
    )


def p4() -> TradePlan:
    """清仓 AAPL，限价 185.00。"""
    return TradePlan(
        plan_id=P4_ID,
        symbol="AAPL",
        sector="科技",
        action=TradeAction.CLOSE,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("185.00"),
    )


def p4_decision() -> RiskDecision:
    """P4 通过，股数 100（全部可卖）。"""
    return RiskDecision(
        outcome=RiskOutcome.PASSED,
        quantity=100,
        emergency_threshold=None,
        expires_at=EXPIRES_AT,
        violations=(),
    )


def p1_request(deadline: datetime = DEADLINE) -> ConfirmationRequest:
    """P1 的确认请求，默认截止时刻 14:30Z。"""
    return ConfirmationRequest(plan=p1(), decision=p1_decision(), deadline=deadline)
