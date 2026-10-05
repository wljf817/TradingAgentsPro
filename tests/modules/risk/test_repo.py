"""预留存储的测试：state-store spec「读取最新版本」里预留表那一行，以及「预留原样读回」。

列出记录的场景同时测内存实现和数据库实现，原样读回只测数据库实现。
数据库实现的用例需要开着 Docker，内存实现的不需要。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine

from tests.fakes.clock import FakeClock
from tests.fakes.reservations import InMemoryReservationStore
from tests.modules.risk.builders import BASELINE_NOW, baseline_plan
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
from tradingagentspro.modules.risk import PostgresReservationStore, Reservation, ReservationStore

EXPIRES_AT = datetime(2026, 10, 14, 20, tzinfo=UTC)


def _reserved() -> Reservation:
    """基准方案通过以后的预留：100 股；方案 ID 每次都是新的。"""
    decision = RiskDecision(
        outcome=RiskOutcome.PASSED,
        quantity=100,
        emergency_threshold=Decimal("0.08"),
        expires_at=EXPIRES_AT,
        violations=(),
    )
    return Reservation(plan=baseline_plan(), decision=decision)


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> ReservationStore:
    """同一组场景同时测内存实现和数据库实现。"""
    if request.param == "memory":
        return InMemoryReservationStore()
    database: Engine = request.getfixturevalue("database")
    return PostgresReservationStore(database, FakeClock(BASELINE_NOW))


def test_no_active_reservations_in_an_empty_table(store: ReservationStore) -> None:
    """spec「表里没有记录时列出」：有效预留为空。"""
    assert store.active() == ()


def test_released_reservation_is_inactive_but_still_readable(store: ReservationStore) -> None:
    """spec「释放的预留不再有效，但仍能读到」。另一个方案的预留仍然有效。"""
    p1, p2 = _reserved(), _reserved()
    store.save(p1)
    store.save(p2)
    store.save(p1.release())

    assert store.active() == (p2,)
    assert store.get(p1.plan.plan_id) == p1.release()


def test_fully_filled_reservation_is_inactive(store: ReservationStore) -> None:
    """spec「全部成交的预留不再有效」。"""
    p1 = _reserved()
    store.save(p1)
    store.save(p1.fill(100))

    assert store.active() == ()


def test_partly_filled_reservation_stays_active(store: ReservationStore) -> None:
    """spec「部分成交的预留仍然有效」。"""
    p1 = _reserved()
    store.save(p1)
    store.save(p1.fill(30))

    assert store.active() == (p1.fill(30),)
    assert store.active()[0].remaining == 70


def test_reservation_reads_back_unchanged(database: Engine) -> None:
    """spec「预留原样读回」：交易方案、调整的风控结果和不通过的规则都不变。"""
    adjusted = RiskDecision(
        outcome=RiskOutcome.ADJUSTED,
        quantity=80,
        emergency_threshold=Decimal("0.08"),
        expires_at=EXPIRES_AT,
        violations=(RiskViolation(rule="已结算资金", message="已结算资金 4,025.00 只够买 80 股"),),
    )
    reserved = Reservation(
        plan=baseline_plan(emergency_threshold=Decimal("0.08")), decision=adjusted
    )
    store = PostgresReservationStore(database, FakeClock(BASELINE_NOW))
    store.save(reserved)

    assert store.get(reserved.plan.plan_id) == reserved
