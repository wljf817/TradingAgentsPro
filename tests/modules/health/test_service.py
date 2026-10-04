"""健康监控用例的测试：健康检查、进入和退出降级模式、恢复后的第一个交易日。

每个测试对应 openspec 的 degraded-mode spec 里的一个场景。
基准数据见 tests/modules/health/builders.py。
"""

from datetime import date, datetime, timedelta

from tests.fakes.clock import FakeClock
from tests.modules.health.builders import BASELINE_NOW, NYSE, at, health_service, signals
from tradingagentspro.modules.health import (
    HealthCheck,
    HealthMode,
    HealthService,
    HealthSignals,
    recovery_day,
)

MODEL_API = HealthCheck.MODEL_API
BROKER = HealthCheck.BROKER
DATA_DELAY = HealthCheck.DATA_DELAY
BUDGET = HealthCheck.BUDGET
SCHEDULER = HealthCheck.SCHEDULER
NORMAL = HealthMode.NORMAL
DEGRADED = HealthMode.DEGRADED
AWAITING = HealthMode.AWAITING_RECONCILIATION


def _disconnected(now: datetime = BASELINE_NOW) -> HealthSignals:
    """在 now 的基准信号，券商断开。"""
    return signals(now, broker_connected=False)


def _abnormal(health: HealthSignals, now: datetime = BASELINE_NOW) -> set[HealthCheck]:
    """用新建的健康监控在 now 判定一次，返回异常的检查。"""
    return set(health_service(FakeClock(now)).check(health).abnormal)


def _service_at(now: datetime = BASELINE_NOW) -> tuple[HealthService, FakeClock]:
    clock = FakeClock(now)
    return health_service(clock), clock


def _awaiting(service: HealthService, clock: FakeClock) -> None:
    """15:00Z 券商断开，判定；15:05Z 券商已连接，判定，等待对账。"""
    service.check(_disconnected())
    clock.set(at(15, 5))
    assert service.check(signals(at(15, 5))).mode is AWAITING


# 健康检查


def test_all_normal() -> None:
    """spec「全部正常」。"""
    assert _abnormal(signals()) == set()


def test_pm_or_critic_without_models() -> None:
    """spec「PM 或 Critic 没有可用的模型」。"""
    assert _abnormal(signals(pm_model_available=False)) == {MODEL_API}
    assert _abnormal(signals(critic_model_available=False)) == {MODEL_API}


def test_broker_disconnected() -> None:
    """spec「券商断开」。"""
    assert _abnormal(_disconnected()) == {BROKER}


def test_quote_delay_boundary() -> None:
    """spec「数据延迟的边界」。"""
    assert _abnormal(signals(last_quote_at=at(14, 59))) == set()
    assert _abnormal(signals(last_quote_at=at(14, 58, 59))) == {DATA_DELAY}


def test_quote_delay_counts_from_the_open() -> None:
    """spec「开盘时从开盘起算延迟」。"""
    yesterday = signals(last_quote_at=at(20, day=13), scheduler_heartbeat_at=at(13, 30))
    never = signals(last_quote_at=None, scheduler_heartbeat_at=at(13, 30))

    assert _abnormal(yesterday, at(13, 31)) == set()
    assert _abnormal(yesterday, at(13, 31, 1)) == {DATA_DELAY}
    assert _abnormal(never, at(13, 31, 1)) == {DATA_DELAY}


def test_quote_delay_is_not_checked_outside_the_session() -> None:
    """spec「盘外不检查数据延迟」。"""
    for now in (at(21), at(12, day=15)):
        health = signals(last_quote_at=at(20), scheduler_heartbeat_at=now - timedelta(minutes=1))

        assert _abnormal(health, now) == set()


def test_reserve_budget_exhausted() -> None:
    """spec「持仓保留预算用完」。"""
    assert _abnormal(signals(reserve_budget_exhausted=True)) == {BUDGET}


def test_scheduler_heartbeat_timeout_boundary() -> None:
    """spec「调度器心跳超时的边界」。"""
    assert _abnormal(signals(scheduler_heartbeat_at=at(14, 55))) == set()
    assert _abnormal(signals(scheduler_heartbeat_at=at(14, 54, 59))) == {SCHEDULER}
    after_hours = signals(last_quote_at=at(20), scheduler_heartbeat_at=at(20, 54, 59))
    assert _abnormal(after_hours, at(21)) == {SCHEDULER}


def test_several_checks_abnormal_at_once() -> None:
    """spec「多项同时异常」。"""
    health = signals(broker_connected=False, reserve_budget_exhausted=True)

    assert _abnormal(health) == {BROKER, BUDGET}


# 进入和退出降级模式


def test_one_abnormal_check_enters_degraded_mode() -> None:
    """spec「一项检查异常就进入降级」。"""
    status = health_service().check(_disconnected())

    assert status.mode is DEGRADED
    assert status.degraded
    assert status.abnormal == {BROKER}
    assert status.degraded_since == at(15)


def test_judging_and_reconciling_while_normal_change_nothing() -> None:
    """spec「正常时判定和对账都不改变状态」。"""
    service = health_service()

    for status in (service.check(signals()), service.reconciled()):
        assert status.mode is NORMAL
        assert not status.degraded
        assert status.degraded_since is None


def test_abnormal_checks_follow_each_judgement_while_degraded() -> None:
    """spec「降级期间异常的检查跟着判定变化」。"""
    service, clock = _service_at()
    service.check(_disconnected())

    clock.set(at(15, 1))
    status = service.check(signals(at(15, 1), reserve_budget_exhausted=True))

    assert status.mode is DEGRADED
    assert status.abnormal == {BUDGET}
    assert status.degraded_since == at(15)


def test_awaiting_reconciliation_after_every_check_recovers() -> None:
    """spec「全部恢复以后等待对账」。"""
    service, clock = _service_at()
    service.check(_disconnected())

    clock.set(at(15, 5))
    status = service.check(signals(at(15, 5)))

    assert status.mode is AWAITING
    assert status.degraded
    assert status.abnormal == set()
    assert status.degraded_since == at(15)


def test_reconciliation_while_degraded_does_not_count() -> None:
    """spec「降级期间完成的对账不算」。"""
    service, clock = _service_at()
    service.check(signals(pm_model_available=False))

    clock.set(at(15, 2))
    assert service.reconciled().mode is DEGRADED

    clock.set(at(15, 5))
    assert service.check(signals(at(15, 5))).mode is AWAITING


def test_reconciliation_while_awaiting_leaves_degraded_mode() -> None:
    """spec「等待对账时对账完成就退出」。"""
    service, clock = _service_at()
    _awaiting(service, clock)

    clock.set(at(15, 6))
    assert service.check(signals(at(15, 6))).mode is AWAITING

    clock.set(at(15, 7))
    status = service.reconciled()
    assert status.mode is NORMAL
    assert not status.degraded
    assert status.degraded_since is None
    assert service.status == status


def test_abnormal_again_while_awaiting_reconciliation() -> None:
    """spec「等待对账时又有检查异常」。"""
    service, clock = _service_at()
    _awaiting(service, clock)

    clock.set(at(15, 6))
    status = service.check(signals(at(15, 6), pm_model_available=False))
    assert status.mode is DEGRADED
    assert status.abnormal == {MODEL_API}
    assert status.degraded_since == at(15)

    clock.set(at(15, 7))
    assert service.reconciled().mode is DEGRADED


def test_judging_again_after_a_restart() -> None:
    """spec「进程重启以后重新判定」：重启是用同一个时钟新建一个健康监控。"""
    service, clock = _service_at()
    service.check(_disconnected())

    restarted = health_service(clock)
    assert restarted.status.mode is NORMAL

    clock.set(at(15, 10))
    status = restarted.check(_disconnected(at(15, 10)))
    assert status.mode is DEGRADED
    assert status.degraded_since == at(15, 10)


# 恢复后的第一个交易日


def test_recovery_before_the_open_or_in_session_is_the_same_day() -> None:
    """spec「盘前和盘中恢复是当天」。"""
    assert recovery_day(at(12), NYSE) == date(2026, 10, 14)
    assert recovery_day(at(19, 59), NYSE) == date(2026, 10, 14)


def test_recovery_at_or_after_the_close_is_the_next_trading_day() -> None:
    """spec「收盘时刻或之后恢复是下一个交易日」。"""
    assert recovery_day(at(20), NYSE) == date(2026, 10, 15)
    assert recovery_day(at(23), NYSE) == date(2026, 10, 15)
    assert recovery_day(at(18, day=27, month=11), NYSE) == date(2026, 11, 30)


def test_recovery_on_a_closed_day() -> None:
    """spec「休市日恢复」。"""
    assert recovery_day(at(15, day=17), NYSE) == date(2026, 10, 19)


# 健康配置


def test_changed_quote_delay_limit() -> None:
    """spec「改数据延迟上限」。"""
    service = health_service(max_quote_delay=timedelta(seconds=120))

    assert service.check(signals(last_quote_at=at(14, 58))).abnormal == set()
    assert service.check(signals(last_quote_at=at(14, 57, 59))).abnormal == {DATA_DELAY}
