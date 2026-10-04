"""代码触发的清仓方案接上风控：检查、重复判定、再校验和熔断。

见 add-emergency-exit 的 design D6、D8。不对应 emergency-exit spec 的场景，
只检查已有的风控行为能接上本模块。账户用风控测试的基准账户：持有 AAA（科技）100 股，最新价 110。
"""

from datetime import UTC, datetime
from decimal import Decimal

from tests.fakes.reservations import InMemoryReservationStore
from tests.modules.emergency.builders import emergency_service
from tests.modules.risk.builders import baseline_account, baseline_plan, holding, risk_service
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.emergency import EmergencyService, Holding
from tradingagentspro.modules.risk import RiskService

PREVIOUS_CLOSE_EQUITY = Decimal(100_000)
# AAA 从前一交易日收盘价 120 跌到 110，跌幅约 0.083，达到紧急阈值 0.08
DROPPED_AAA = Holding(
    symbol="AAA",
    sector="科技",
    emergency_threshold=Decimal("0.08"),
    previous_close=Decimal(120),
    last_price=Decimal(110),
)


def _check(risk: RiskService, plan: TradePlan, emergency: EmergencyService) -> RiskDecision:
    """用基准账户检查方案：熔断状态取紧急退出的，不处于降级模式。"""
    return risk.check(
        plan,
        baseline_account(),
        daily_loss_breaker_tripped=emergency.breaker_tripped(),
        degraded=False,
    )


def _close_aaa(emergency: EmergencyService, equity: int = 99_000) -> TradePlan:
    """判定一次，返回 AAA 的清仓方案。"""
    (exit_,) = emergency.check([DROPPED_AAA], Decimal(equity), PREVIOUS_CLOSE_EQUITY)
    return exit_.plan


def test_close_plan_passes_risk_for_the_sellable_quantity() -> None:
    emergency = emergency_service()
    risk = risk_service(InMemoryReservationStore())

    decision = _check(risk, _close_aaa(emergency), emergency)

    assert (decision.outcome, decision.quantity) == (RiskOutcome.PASSED, 100)
    assert decision.expires_at == datetime(2026, 10, 14, 20, tzinfo=UTC)  # 当天收盘


def test_repeated_judgement_keeps_a_single_reservation() -> None:
    emergency = emergency_service()
    store = InMemoryReservationStore()
    risk = risk_service(store)
    first = _check(risk, _close_aaa(emergency), emergency)

    again = _check(risk, _close_aaa(emergency), emergency)

    assert again == first
    assert len(store.active()) == 1


def test_recheck_sells_the_live_holding() -> None:
    emergency = emergency_service()
    risk = risk_service(InMemoryReservationStore())
    plan = _close_aaa(emergency)
    _check(risk, plan, emergency)
    live = baseline_account(positions=(holding("AAA", "科技", 60, 100, 110, 90),))

    decision = risk.recheck(
        plan.plan_id,
        live,
        Decimal(110),
        daily_loss_breaker_tripped=emergency.breaker_tripped(),
        degraded=False,
    )

    assert (decision.outcome, decision.quantity) == (RiskOutcome.PASSED, 60)


def test_breaker_rejects_opening_but_not_the_close() -> None:
    emergency = emergency_service()
    risk = risk_service(InMemoryReservationStore())
    close = _close_aaa(emergency, equity=97_000)
    tripped = emergency.breaker_tripped()

    opening = _check(risk, baseline_plan(), emergency)
    closing = _check(risk, close, emergency)

    assert tripped
    assert opening.outcome is RiskOutcome.REJECTED
    assert "单日亏损熔断" in [v.rule for v in opening.violations]
    assert (closing.outcome, closing.quantity) == (RiskOutcome.PASSED, 100)
