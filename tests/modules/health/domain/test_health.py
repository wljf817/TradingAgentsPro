"""健康监控纯逻辑的测试，直接构造健康信号。

场景见 openspec 的 degraded-mode spec「健康检查」「进入和退出降级模式」「恢复后的第一个交易日」。
"""

from datetime import date, datetime, timedelta

import pytest
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from tests.modules.health.builders import BASELINE_NOW, NYSE, at, signals
from tradingagentspro.modules.health import (
    HealthCheck,
    HealthConfig,
    HealthMode,
    HealthSignals,
    HealthStatus,
    abnormal_checks,
    recovery_day,
)

CONFIG = HealthConfig()


def _abnormal(health: HealthSignals, now: datetime = BASELINE_NOW) -> frozenset[HealthCheck]:
    """在 now 用默认配置判定这些信号。"""
    return abnormal_checks(health, now, NYSE.session_at(now), CONFIG)


# 健康检查


def test_all_checks_normal() -> None:
    assert _abnormal(signals()) == frozenset()


def test_pm_without_models_is_a_model_api_failure() -> None:
    assert _abnormal(signals(pm_model_available=False)) == {HealthCheck.MODEL_API}


def test_critic_without_models_is_a_model_api_failure() -> None:
    assert _abnormal(signals(critic_model_available=False)) == {HealthCheck.MODEL_API}


def test_broker_disconnected() -> None:
    assert _abnormal(signals(broker_connected=False)) == {HealthCheck.BROKER}


def test_reserve_budget_exhausted() -> None:
    assert _abnormal(signals(reserve_budget_exhausted=True)) == {HealthCheck.BUDGET}


def test_quote_delay_equal_to_the_limit_is_normal() -> None:
    assert _abnormal(signals(last_quote_at=at(14, 59))) == frozenset()


def test_quote_delay_over_the_limit_is_abnormal() -> None:
    assert _abnormal(signals(last_quote_at=at(14, 58, 59))) == {HealthCheck.DATA_DELAY}


@pytest.mark.parametrize("last_quote_at", [at(20, day=13), None])
def test_quote_delay_counts_from_the_open(last_quote_at: datetime | None) -> None:
    """昨天的行情和从来没有行情，都从今天开盘 13:30Z 起算。"""
    health = signals(last_quote_at=last_quote_at, scheduler_heartbeat_at=at(13, 30))

    assert _abnormal(health, at(13, 31)) == frozenset()
    assert _abnormal(health, at(13, 31, 1)) == {HealthCheck.DATA_DELAY}


@pytest.mark.parametrize("now", [at(21), at(12, day=15)])
def test_quote_delay_is_not_checked_outside_the_session(now: datetime) -> None:
    """盘后和盘前：行情最近一次更新在 2026-10-14 20:00Z，调度器心跳在 1 分钟前。"""
    health = signals(last_quote_at=at(20), scheduler_heartbeat_at=now - timedelta(minutes=1))

    assert _abnormal(health, now) == frozenset()


def test_heartbeat_equal_to_the_timeout_is_normal() -> None:
    assert _abnormal(signals(scheduler_heartbeat_at=at(14, 55))) == frozenset()


def test_heartbeat_over_the_timeout_is_abnormal() -> None:
    assert _abnormal(signals(scheduler_heartbeat_at=at(14, 54, 59))) == {HealthCheck.SCHEDULER}


def test_heartbeat_is_checked_outside_the_session() -> None:
    health = signals(last_quote_at=at(20), scheduler_heartbeat_at=at(20, 54, 59))

    assert _abnormal(health, at(21)) == {HealthCheck.SCHEDULER}


def test_several_checks_abnormal_at_once() -> None:
    health = signals(broker_connected=False, reserve_budget_exhausted=True)

    assert _abnormal(health) == {HealthCheck.BROKER, HealthCheck.BUDGET}


# 恢复后的第一个交易日


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (at(12), date(2026, 10, 14)),  # 盘前
        (at(13, 30), date(2026, 10, 14)),  # 开盘
        (at(19, 59), date(2026, 10, 14)),  # 盘中
        (at(20), date(2026, 10, 15)),  # 收盘时刻
        (at(23), date(2026, 10, 15)),  # 收盘之后
        (at(18, day=27, month=11), date(2026, 11, 30)),  # 感恩节次日提前收盘的收盘时刻
        (at(15, day=17), date(2026, 10, 19)),  # 周六
    ],
)
def test_recovery_day(moment: datetime, expected: date) -> None:
    assert recovery_day(moment, NYSE) == expected


# 降级模式的状态

NORMAL = HealthMode.NORMAL
DEGRADED = HealthMode.DEGRADED
AWAITING = HealthMode.AWAITING_RECONCILIATION
BROKER = frozenset({HealthCheck.BROKER})
BUDGET = frozenset({HealthCheck.BUDGET})
NONE: frozenset[HealthCheck] = frozenset()
ENTERED = at(15)
LATER = at(15, 5)


def _degraded(abnormal: frozenset[HealthCheck] = BROKER) -> HealthStatus:
    """15:00Z 进入降级的状态。"""
    return HealthStatus().judged(abnormal, ENTERED)


def _awaiting() -> HealthStatus:
    """15:00Z 进入降级，15:05Z 全部恢复，等待对账。"""
    return _degraded().judged(NONE, LATER)


def test_new_status_is_normal() -> None:
    status = HealthStatus()

    assert (status.mode, status.abnormal, status.degraded_since) == (NORMAL, NONE, None)
    assert not status.degraded


def test_normal_with_abnormal_checks_enters_degraded() -> None:
    """状态表"正常 | 判定，有异常的检查"。"""
    status = HealthStatus().judged(BROKER, ENTERED)

    assert (status.mode, status.abnormal, status.degraded_since) == (DEGRADED, BROKER, ENTERED)
    assert status.degraded


def test_normal_stays_normal_after_a_clean_judgement_or_a_reconciliation() -> None:
    """状态表"正常 | 判定，没有异常的检查"和"正常 | 对账完成"。"""
    assert HealthStatus().judged(NONE, ENTERED) == HealthStatus()
    assert HealthStatus().reconciled() == HealthStatus()


def test_degraded_with_abnormal_checks_follows_the_latest_judgement() -> None:
    """状态表"降级 | 判定，有异常的检查"：进入时刻不变。"""
    status = _degraded().judged(BUDGET, LATER)

    assert (status.mode, status.abnormal, status.degraded_since) == (DEGRADED, BUDGET, ENTERED)


def test_degraded_without_abnormal_checks_awaits_reconciliation() -> None:
    """状态表"降级 | 判定，没有异常的检查"。"""
    status = _awaiting()

    assert (status.mode, status.abnormal, status.degraded_since) == (AWAITING, NONE, ENTERED)
    assert status.degraded


def test_reconciliation_while_degraded_does_not_count() -> None:
    """状态表"降级 | 对账完成"。"""
    assert _degraded().reconciled() == _degraded()


def test_awaiting_with_abnormal_checks_is_degraded_again() -> None:
    """状态表"等待对账 | 判定，有异常的检查"：进入时刻不变。"""
    status = _awaiting().judged(BUDGET, at(15, 6))

    assert (status.mode, status.abnormal, status.degraded_since) == (DEGRADED, BUDGET, ENTERED)


def test_awaiting_stays_awaiting_after_a_clean_judgement() -> None:
    """状态表"等待对账 | 判定，没有异常的检查"。"""
    assert _awaiting().judged(NONE, at(15, 6)) == _awaiting()


def test_reconciliation_while_awaiting_returns_to_normal() -> None:
    """状态表"等待对账 | 对账完成"：进入时刻清掉。"""
    status = _awaiting().reconciled()

    assert status == HealthStatus()
    assert not status.degraded


# 状态机性质测试（design D7）：参考模型直接按 spec 的状态表写

_TABLE = {
    (NORMAL, "abnormal"): DEGRADED,
    (NORMAL, "clean"): NORMAL,
    (NORMAL, "reconciled"): NORMAL,
    (DEGRADED, "abnormal"): DEGRADED,
    (DEGRADED, "clean"): AWAITING,
    (DEGRADED, "reconciled"): DEGRADED,
    (AWAITING, "abnormal"): DEGRADED,
    (AWAITING, "clean"): AWAITING,
    (AWAITING, "reconciled"): NORMAL,
}


class DegradedModeMachine(RuleBasedStateMachine):
    """随机交错判定（有或没有异常的检查）、对账完成和拨动时间，和按状态表写的参考模型对照。"""

    def __init__(self) -> None:
        super().__init__()
        self.status = HealthStatus()
        self.now = BASELINE_NOW
        self.mode = NORMAL  # 参考模型的状态
        self.since: datetime | None = None  # 参考模型的进入时刻

    def _step(self, event_name: str, after: HealthStatus) -> None:
        expected = _TABLE[(self.mode, event_name)]
        if (self.mode, expected) == (AWAITING, NORMAL):
            event("等待对账时对账完成回到正常")
        if (self.mode, expected) == (AWAITING, DEGRADED):
            event("等待对账时又有异常")
        if expected is NORMAL:
            self.since = None
        elif self.since is None:
            self.since = self.now
        self.mode = expected
        self.status = after

    @rule(abnormal=st.frozensets(st.sampled_from(HealthCheck)))
    def judge(self, abnormal: frozenset[HealthCheck]) -> None:
        after = self.status.judged(abnormal, self.now)
        self._step("abnormal" if abnormal else "clean", after)
        assert after.abnormal == abnormal

    @rule()
    def reconcile(self) -> None:
        self._step("reconciled", self.status.reconciled())

    @rule(seconds=st.integers(1, 600))
    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @invariant()
    def matches_the_table(self) -> None:
        assert self.status.mode is self.mode
        assert self.status.degraded is (self.mode is not NORMAL)
        assert self.status.degraded_since == self.since

    @invariant()
    def abnormal_checks_mean_degraded(self) -> None:
        if self.status.abnormal:
            assert self.status.mode is DEGRADED


TestDegradedModeMachine = DegradedModeMachine.TestCase
