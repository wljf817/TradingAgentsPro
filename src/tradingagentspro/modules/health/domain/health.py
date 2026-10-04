"""系统健康：五项健康检查、降级模式的三种状态，以及恢复后的第一个交易日。

见 degraded-mode spec。健康信号都由调用方传入。
"""

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from tradingagentspro.modules.health.config import HealthConfig
from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession


class HealthCheck(StrEnum):
    """五项健康检查，见 degraded-mode spec「健康检查」。"""

    MODEL_API = "model_api"
    BROKER = "broker"
    DATA_DELAY = "data_delay"
    BUDGET = "budget"
    SCHEDULER = "scheduler"


@dataclass(frozen=True, slots=True)
class HealthSignals:
    """一次判定用的健康信号，由调用方从各个来源拼好，不加约束。

    - pm_model_available、critic_model_available：还有可用的模型，主模型或者任何一个备用模型都算
    - last_quote_at：行情最近一次更新的时刻；从来没有更新过时为 None
    - reserve_budget_exhausted：持仓保留预算用完
    - scheduler_heartbeat_at：调度器最近一次心跳的时刻
    """

    pm_model_available: bool
    critic_model_available: bool
    broker_connected: bool
    last_quote_at: datetime | None
    reserve_budget_exhausted: bool
    scheduler_heartbeat_at: datetime


class HealthMode(StrEnum):
    """健康监控的三种状态，见 spec「进入和退出降级模式」。降级和等待对账都算降级模式。"""

    NORMAL = "normal"
    DEGRADED = "degraded"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"  # 检查都恢复了，等一次对账


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """健康监控的当前状态。

    - abnormal：最近一次判定时异常的检查
    - degraded_since：进入降级模式的时刻，经过等待对账再回到降级也不变；正常时为 None

    状态变化的方法都返回新的状态。
    """

    mode: HealthMode = HealthMode.NORMAL
    abnormal: frozenset[HealthCheck] = frozenset()
    degraded_since: datetime | None = None

    @property
    def degraded(self) -> bool:
        """处于降级模式：降级或等待对账。"""
        return self.mode is not HealthMode.NORMAL

    def judged(self, abnormal: frozenset[HealthCheck], now: datetime) -> Self:
        """在 now 判定了一次，异常的检查是 abnormal：有异常就是降级，降级时全部恢复就等待对账。"""
        if abnormal:
            return replace(
                self,
                mode=HealthMode.DEGRADED,
                abnormal=abnormal,
                degraded_since=self.degraded_since or now,
            )
        if self.mode is HealthMode.NORMAL:
            return self
        return replace(self, mode=HealthMode.AWAITING_RECONCILIATION, abnormal=abnormal)

    def reconciled(self) -> Self:
        """完成了一次对账：只有等待对账时回到正常，降级期间完成的对账不算。"""
        if self.mode is HealthMode.AWAITING_RECONCILIATION:
            return type(self)()
        return self


def abnormal_checks(
    signals: HealthSignals, now: datetime, session: TradingSession | None, config: HealthConfig
) -> frozenset[HealthCheck]:
    """这次判定异常的检查。session 是当前时刻所在的常规时段，盘外为 None，这时不检查数据延迟。

    行情延迟从行情最近一次更新和今天开盘两者中较晚的那个起算；超时都是超过才算异常。
    """
    abnormal: set[HealthCheck] = set()
    if not (signals.pm_model_available and signals.critic_model_available):
        abnormal.add(HealthCheck.MODEL_API)
    if not signals.broker_connected:
        abnormal.add(HealthCheck.BROKER)
    if session is not None:
        since = max(session.open, signals.last_quote_at or session.open)
        if now - since > config.max_quote_delay:
            abnormal.add(HealthCheck.DATA_DELAY)
    if signals.reserve_budget_exhausted:
        abnormal.add(HealthCheck.BUDGET)
    if now - signals.scheduler_heartbeat_at > config.scheduler_heartbeat_timeout:
        abnormal.add(HealthCheck.SCHEDULER)
    return frozenset(abnormal)


def recovery_day(moment: datetime, calendar: TradingCalendar) -> date:
    """恢复后的第一个交易日：收盘时刻晚于 moment 的第一个交易日。

    盘中是当天；盘前、收盘时刻或之后、休市日，是开盘晚于 moment 的第一个交易日。
    """
    session = calendar.session_at(moment) or calendar.next_session_after(moment)
    return session.day
