"""代码触发的清仓方案接上风控：检查、重复判定、再校验和熔断。

见 add-emergency-exit 的 design D6、D8。不对应 emergency-exit spec 的场景，
只检查已有的风控行为能接上本模块。账户用风控测试的基准账户：持有 AAA（科技）100 股，最新价 110。
"""

from datetime import UTC, datetime
from decimal import Decimal

from tests.fakes.reservations import InMemoryReservationStore
from tests.modules.emergency.builders import emergency_service
from tests.modules.risk.builders import baseline_account, baseline_plan, holding, risk_service
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.emergency import EmergencyService, Holding

PREVIOUS_CLOSE_EQUITY = Decimal(100_000)
# AAA 从前一交易日收盘价 120 跌到 110，跌幅约 0.083，达到紧急阈值 0.08
DROPPED_AAA = Holding(
    symbol="AAA",
    sector="科技",
    emergency_threshold=Decimal("0.08"),
    previous_close=Decimal(120),
    last_price=Decimal(110),
)


def _close_aaa(emergency: EmergencyService, equity: int = 99_000) -> TradePlan:
    """判定一次，返回 AAA 的清仓方案。"""
    (exit_,) = emergency.check([DROPPED_AAA], Decimal(equity), PREVIOUS_CLOSE_EQUITY)
    return exit_.plan


def test_close_plan_passes_risk_for_the_sellable_quantity() -> None:
    emergency = emergency_service()
    risk = risk_service(InMemoryReservationStore())

    decision = risk.check(_close_aaa(emergency), baseline_account(), emergency.breaker_tripped())

    assert (decision.outcome, decision.quantity) == (RiskOutcome.PASSED, 100)
    assert decision.expires_at == datetime(2026, 10, 14, 20, tzinfo=UTC)  # 当天收盘


def test_repeated_judgement_keeps_a_single_reservation() -> None:
    emergency = emergency_service()
    store = InMemoryReservationStore()
    risk = risk_service(store)
    first = risk.check(_close_aaa(emergency), baseline_account(), emergency.breaker_tripped())

    again = risk.check(_close_aaa(emergency), baseline_account(), emergency.breaker_tripped())

    assert again == first
    assert len(store.active()) == 1


def test_recheck_sells_the_live_holding() -> None:
    emergency = emergency_service()
    risk = risk_service(InMemoryReservationStore())
    plan = _close_aaa(emergency)
    risk.check(plan, baseline_account(), emergency.breaker_tripped())
    live = baseline_account(positions=(holding("AAA", "科技", 60, 100, 110, 90),))

    decision = risk.recheck(plan.plan_id, live, Decimal(110), emergency.breaker_tripped())

    assert (decision.outcome, decision.quantity) == (RiskOutcome.PASSED, 60)


def test_breaker_rejects_opening_but_not_the_close() -> None:
    emergency = emergency_service()
    risk = risk_service(InMemoryReservationStore())
    close = _close_aaa(emergency, equity=97_000)
    tripped = emergency.breaker_tripped()

    opening = risk.check(baseline_plan(), baseline_account(), tripped)
    closing = risk.check(close, baseline_account(), tripped)

    assert tripped
    assert opening.outcome is RiskOutcome.REJECTED
    assert "单日亏损熔断" in [v.rule for v in opening.violations]
    assert (closing.outcome, closing.quantity) == (RiskOutcome.PASSED, 100)
