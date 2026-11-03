"""收盘心跳：每个交易日收盘以后做一次——移出过期的候选，刷新股票池里股票的财报，组出快照做收盘检查，
把检查项作为定时心跳的触发排进队列，写程序日志的收盘检查。

见 close-heartbeat spec 和 add-close-heartbeat 的 design D1 到 D6。CloseHeartbeat 在自己的守护线程
里每隔一个间隔判断一次：到期（heartbeat_day）而这一天还没开始过，就开始这一天的 DBOS 工作流
close_heartbeat，不等它做完。工作流 ID 由交易日定（DbosHeartbeatRuns）：同一天只开始一次，做到
一半停了由下次 launch 的 DBOS 接着做完。

工作流分 step：移出、读股票池、每只股票一次刷新（中断以后已经刷新的按记下的输出重放，不再请求），
最后收盘检查连同排队和写日志一个 step——它中途中断、恢复以后重做时，已经排进的在队列里，收盘检查不再
给出它们。相邻两次刷新之间等 install 交进来的持久化等待。规则全在定时心跳模块的纯函数里，这里只读
数据、组快照、排队和写日志。收盘心跳不调用模型、不做交易判断、不下单、不拿全程序共用的那把锁。
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from uuid import UUID

from dbos import DBOS, SetWorkflowID
from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.caserecords import CaseRecord, CaseRecordService
from tradingagentspro.modules.companydata import CompanyDataService, CompanyDataUnavailableError
from tradingagentspro.modules.heartbeat import (
    CheckItem,
    CloseSnapshot,
    HeartbeatConfig,
    PoolCandidate,
    ThesisDates,
    close_check,
    heartbeat_day,
)
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
from tradingagentspro.modules.scheduler import (
    HEARTBEAT_CASE_TYPES,
    HeartbeatTrigger,
    QueueService,
    Trigger,
)
from tradingagentspro.modules.stockpool import PoolEntry, StockPoolService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows.case_entries import case_trigger
from tradingagentspro.workflows.case_workflow import CaseProgress

_log = logging.getLogger(__name__)


class CloseHeartbeatConfig(BaseModel):
    """收盘心跳的配置和默认值，含义见 close-heartbeat spec「收盘心跳的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每隔这么久判断一次要不要做收盘心跳
    interval: timedelta = Field(default=timedelta(seconds=60), gt=timedelta(0))
    # 收盘以后这么久才做：排在收盘对账之后
    delay: timedelta = Field(default=timedelta(minutes=15), ge=timedelta(0))
    # 刷新财报时相邻两只股票之间等这么久：留在 Finnhub 每分钟 60 次以内
    refresh_gap: timedelta = Field(default=timedelta(seconds=3), gt=timedelta(0))


class HeartbeatIntake(Protocol):
    """收盘心跳排案件的地方；生产是排队运行，测试换成记下排队的替身。"""

    def enqueue(self, trigger: Trigger, triggered_at: datetime) -> UUID:
        """排进一个案件，交回案件 ID。"""
        ...


@dataclass(frozen=True, slots=True)
class HeartbeatParts:
    """收盘心跳的工作流用到的部件。queue 只用来读排队的案件；running 给出运行中的案件。"""

    pool: StockPoolService
    company_data: CompanyDataService
    records: CaseRecordService
    queue: QueueService
    running: Callable[[], Sequence[tuple[UUID, CaseProgress]]]
    intake: HeartbeatIntake
    log: ProgramLog
    clock: Clock
    calendar: TradingCalendar
    heartbeat: HeartbeatConfig
    config: CloseHeartbeatConfig


Wait = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class _Installed:
    parts: HeartbeatParts
    wait: Wait


_installed: _Installed | None = None


def install(parts: HeartbeatParts, wait: Wait = DBOS.sleep) -> None:
    """DBOS launch 之前调用：之后的收盘心跳（包括恢复的）都用这些部件。

    wait 是相邻两次刷新之间的持久化等待：生产用 DBOS.sleep，测试换成拨钟的替身。
    """
    global _installed
    _installed = _Installed(parts, wait)


def _parts() -> HeartbeatParts:
    if _installed is None:
        raise RuntimeError("收盘心跳还没有 install")
    return _installed.parts


# 工作流


@DBOS.workflow(name="close_heartbeat")
def close_heartbeat(day: date) -> None:
    """day 的一次收盘心跳，见 spec「一次收盘心跳」。出错写 ERROR 再抛出，这一天不再做。"""
    try:
        _expire()
        symbols = _pool_symbols()
        assert _installed is not None  # _pool_symbols 已经读过部件
        gap = _installed.parts.config.refresh_gap.total_seconds()
        failures: list[tuple[str, str]] = []
        for i, symbol in enumerate(symbols):
            if i:
                _installed.wait(gap)
            reason = _refresh(symbol)
            if reason is not None:
                failures.append((symbol, reason))
        _check(day, len(symbols), tuple(failures))
    except Exception:
        _log.exception("收盘心跳 %s 出错，这一天不再做", day)
        raise


@DBOS.step(name="close_heartbeat.expire")
def _expire() -> None:
    """第 1 步：移出到期的候选，股票池写超期移出。"""
    _parts().pool.expire_candidates()


@DBOS.step(name="close_heartbeat.symbols")
def _pool_symbols() -> tuple[str, ...]:
    """第 2 步要刷新的股票：这时股票池里的股票，按代码。"""
    return tuple(sorted(e.symbol for e in _parts().pool.entries()))


@DBOS.step(name="close_heartbeat.refresh")
def _refresh(symbol: str) -> str | None:
    """刷新一只股票的财报；取不到时写 WARNING，交回原因，见 spec「刷新财报」。"""
    try:
        _parts().company_data.refresh_earnings(symbol)
    except CompanyDataUnavailableError as error:
        _log.warning("收盘心跳：%s 的财报没有刷新：%s", symbol, error)
        return str(error)
    return None


@DBOS.step(name="close_heartbeat.check")
def _check(day: date, symbols: int, failures: tuple[tuple[str, str], ...]) -> None:
    """第 3、4 步：读时钟、组快照、收盘检查，按顺序排进，最后写收盘检查。"""
    parts = _parts()
    at = parts.clock.now()
    items = close_check(_snapshot(parts, day), parts.calendar, parts.heartbeat)
    triggers = [HeartbeatTrigger(item.case_type, item.symbol) for item in items]
    case_ids = [str(parts.intake.enqueue(trigger, at)) for trigger in triggers]
    detail = _check_text(triggers, at, symbols, failures)
    parts.log.write(Actor.SCHEDULER, Action.CLOSE_CHECK, day.isoformat(), detail, outputs=case_ids)


def _snapshot(parts: HeartbeatParts, day: date) -> CloseSnapshot:
    """收盘检查的快照，见 spec「收盘检查的快照」：股票池只读一次，持仓、关注、候选都从它分出来。"""
    entries = parts.pool.entries()
    last = parts.records.last_concluded(CaseType.EXPLORATION, None)
    last_day = None
    if last is not None:
        last_day = parts.calendar.session_closing_after(_concluded_at(last)).day
    return CloseSnapshot(
        today=day,
        holdings=tuple(_dates(e) for e in entries if e.status is StockStatus.HOLDING),
        watches=tuple(_dates(e) for e in entries if e.status is StockStatus.WATCH),
        candidates=tuple(
            _candidate(e, parts.records) for e in entries if e.status is StockStatus.CANDIDATE
        ),
        last_exploration_day=last_day,
        in_flight=_in_flight(parts),
    )


def _dates(entry: PoolEntry) -> ThesisDates:
    """持仓用投资论点、关注用关注论点的复查日和催化剂截止日期。"""
    thesis = entry.investment_thesis if entry.status is StockStatus.HOLDING else entry.watch_thesis
    assert thesis is not None  # 股票池的不变量：持仓有投资论点，关注有关注论点
    return ThesisDates(entry.symbol, thesis.review_day, thesis.catalyst.deadline)


def _candidate(entry: PoolEntry, records: CaseRecordService) -> PoolCandidate:
    """一只候选；新候选按 spec 的表：有成为候选的时刻，并且这以后没有候选评估得出结论。"""
    assert entry.expires_at is not None  # 股票池的不变量：候选有过期时间
    since = entry.candidate_since
    new = False
    if since is not None:
        last = records.last_concluded(CaseType.CANDIDATE_EVALUATION, entry.symbol)
        new = last is None or _concluded_at(last) < since
    return PoolCandidate(entry.symbol, entry.score, entry.expires_at, new)


def _concluded_at(record: CaseRecord) -> datetime:
    """last_concluded 给出的案件的结论时刻。"""
    assert record.conclusion is not None  # last_concluded 只给出得出结论的案件
    return record.conclusion.at


def _in_flight(parts: HeartbeatParts) -> frozenset[CheckItem]:
    """队列里触发是定时心跳的，加上运行中的案件里案件类型是定时心跳开出的那几种。

    运行中的案件不带触发；那几种案件类型只有定时心跳开得出，按类型认（design D3）。
    """
    queued = {
        CheckItem(case.trigger.case_type, case.trigger.symbol)
        for case in parts.queue.queued()
        if isinstance(case.trigger, HeartbeatTrigger)
    }
    running = {
        CheckItem(progress.case_type, progress.symbol)
        for _, progress in parts.running()
        if progress.case_type in HEARTBEAT_CASE_TYPES
    }
    return frozenset(queued | running)


def _check_text(
    triggers: Sequence[HeartbeatTrigger],
    at: datetime,
    symbols: int,
    failures: Sequence[tuple[str, str]],
) -> str:
    """收盘检查的说明，见 program-log spec「调度器的动作」：开出的检查项和刷新财报的结果。"""
    if triggers:
        reasons = "、".join(case_trigger(trigger, at).reason for trigger in triggers)
        opened = f"开出 {len(triggers)} 个检查项：{reasons}"
    else:
        opened = "没有要开的检查项"
    missed = "".join(f"；{symbol} 没有刷新：{reason}" for symbol, reason in failures)
    return f"{opened}。刷新了 {symbols - len(failures)} 只股票的财报{missed}"


# 判断的线程


class HeartbeatRuns(Protocol):
    """每个交易日的收盘心跳开始过没有、开始它；生产是 DbosHeartbeatRuns。"""

    def started(self, day: date) -> bool:
        """day 的收盘心跳开始过：做完的、正在做的、出错停下的都算。"""
        ...

    def start(self, day: date) -> None:
        """开始 day 的收盘心跳，不等它做完。"""
        ...


class DbosHeartbeatRuns:
    """满足 HeartbeatRuns：一天一个 DBOS 工作流，工作流 ID 由交易日定，见 design D1。"""

    def started(self, day: date) -> bool:
        return DBOS.get_workflow_status(_workflow_id(day)) is not None

    def start(self, day: date) -> None:
        with SetWorkflowID(_workflow_id(day)):
            DBOS.start_workflow(close_heartbeat, day)


def _workflow_id(day: date) -> str:
    return f"close-heartbeat-{day.isoformat()}"


class CloseHeartbeat:
    """收盘心跳的判断，见 spec「什么时候做」。

    check 判断一次，可以直接调用；start 开守护线程循环，立即判断第一次；stop 停下并等线程结束，
    不等已经开始的收盘心跳。
    """

    def __init__(
        self,
        runs: HeartbeatRuns,
        clock: Clock,
        calendar: TradingCalendar,
        config: CloseHeartbeatConfig,
    ) -> None:
        self._runs = runs
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """开守护线程 close-heartbeat，立即判断第一次。写一条 INFO：收盘心跳已开始和间隔。"""
        self._thread = threading.Thread(target=self._loop, name="close-heartbeat", daemon=True)
        self._thread.start()
        _log.info("收盘心跳已开始，间隔 %g 秒", self._config.interval.total_seconds())

    def stop(self) -> None:
        """不再判断，等正在做的判断做完、线程结束；没有开始过时什么都不做。"""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()

    def check(self) -> None:
        """判断一次：到期而这一天还没开始过，就开始这一天的收盘心跳，写一条 INFO。"""
        day = heartbeat_day(self._calendar, self._clock.now(), self._config.delay)
        if day is None or self._runs.started(day):
            return
        self._runs.start(day)
        _log.info("收盘心跳 %s 开始", day)

    def _loop(self) -> None:
        """判断一次，等一个间隔，直到停止；一次判断出错写 ERROR，下一次照常。"""
        seconds = self._config.interval.total_seconds()
        while not self._stopping.is_set():
            try:
                self.check()
            except Exception:
                _log.exception("收盘心跳：这次判断出错")
            self._stopping.wait(seconds)
