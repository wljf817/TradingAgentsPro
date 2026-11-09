"""Quant 任务的量化指标：由 Task 和这次运行读的快照组出请求，经行情模块算出，满足 Agent 网关的
QuantNumbers 端口。见 task-dispatch spec「Quant 任务的量化指标」和 update-quant-entry 的 design D2、
D3。
"""

from tradingagentspro.contracts.quant import QuantIndicators
from tradingagentspro.contracts.snapshot import EventEntry, Snapshot
from tradingagentspro.contracts.task import Task
from tradingagentspro.modules.gateway import QuantDataUnavailableError
from tradingagentspro.modules.marketdata import (
    BarsUnavailableError,
    EventTime,
    IndicatorRequest,
    IndicatorService,
)


def indicator_request(task: Task, snapshot: Snapshot) -> IndicatorRequest:
    """按 spec 的表由 Task 和快照组出算量化指标的请求。task 是 Quant 的任务。"""
    assert task.bars is not None  # Quant 的任务都写了周期和范围
    quote = next((q for q in snapshot.quotes if q.symbol == task.symbol), None)
    found = (snapshot.entry(entry_id) for entry_id in task.inputs)
    return IndicatorRequest(
        symbol=task.symbol,
        sector=_sector(task.symbol, snapshot),
        window=task.bars,
        taken_at=snapshot.taken_at,
        prices={q.symbol: q.last_price for q in snapshot.quotes if q.last_price is not None},
        volume_ratio=None if quote is None else quote.volume_ratio,
        events=tuple(
            EventTime(entry.event.event_id, entry.event.occurred_at)
            for entry in found
            if isinstance(entry, EventEntry)
        ),
    )


def _sector(symbol: str, snapshot: Snapshot) -> str | None:
    """股票池里的板块；不在股票池里时取粗筛名单里的；都没有时为 None（design D3）。"""
    member = next((m for m in snapshot.pool.members if m.symbol == symbol), None)
    if member is not None:
        return member.sector
    candidates = () if snapshot.screen is None else snapshot.screen.candidates
    candidate = next((c for c in candidates if c.symbol == symbol), None)
    return None if candidate is None else candidate.listing.sector


class SnapshotIndicators:
    """满足 Agent 网关的 QuantNumbers：K 线源不可用换成网关的 QuantDataUnavailableError，说明是行情
    模块给出的原因；其他错误原样抛出。
    """

    def __init__(self, indicators: IndicatorService) -> None:
        self._indicators = indicators

    def indicators(self, task: Task, snapshot: Snapshot) -> QuantIndicators:
        try:
            return self._indicators.indicators(indicator_request(task, snapshot))
        except BarsUnavailableError as error:
            raise QuantDataUnavailableError(str(error)) from error
