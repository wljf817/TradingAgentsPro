"""Agent 网关测试的数据：signal-format spec「信号的格式」的基准数据，以及 case-types spec 的结论的
公共部分（信号是基准信号）。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tradingagentspro.contracts.conclusion import (
    CandidateConclusion,
    Decision,
    ExplorationConclusion,
    HoldingConclusion,
    PoolAddition,
    WatchConclusion,
)
from tradingagentspro.contracts.signal import NoSignal, Signal


def at(hour: int, minute: int = 0, second: int = 0, *, day: int = 14) -> datetime:
    """2026 年 10 月某一天的某个 UTC 时刻，默认是 2026-10-14。"""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


NOW = at(15)  # 当前时刻：周三，盘中

_SIGNAL: dict[str, Any] = {
    "horizon": 5,
    "score": 1,
    "confidence": Decimal("0.6"),
    "data_as_of": at(14, 30),
    "cited_entries": ("E-1", "E-2"),
    "rationale": "订单回暖，放量突破前高",
    "risks": "板块整体回调时跟跌",
}


def signal(**changes: Any) -> Signal:
    """只把基准信号的这几项改为 changes 的信号，比如 signal(horizon=4)。"""
    return Signal.model_validate(_SIGNAL | changes)


SIGNAL = signal()  # 基准信号
NO_SIGNAL = NoSignal(reason="最近一期财报还没有进状态库，无法判断")  # 基准无信号


# 结论的公共部分里信号以外的字段：回答、理由、引用 R-1 和 R-2
_COMMON: dict[str, Any] = {
    "answer": "论点仍然成立",
    "rationale": "订单和毛利率都符合预期",
    "cited_results": ("R-1", "R-2"),
}


def _common(signal: Signal) -> dict[str, Any]:
    """结论的公共部分，信号是 signal。"""
    return _COMMON | {"signal": signal}


def holding(signal: Signal = SIGNAL) -> HoldingConclusion:
    """持仓的结论：持有。"""
    return HoldingConclusion.model_validate(_common(signal) | {"decision": Decision.HOLD})


def watch(signal: Signal = SIGNAL) -> WatchConclusion:
    """关注的结论：继续关注。"""
    return WatchConclusion.model_validate(_common(signal) | {"decision": Decision.KEEP_WATCHING})


def candidate(signal: Signal = SIGNAL) -> CandidateConclusion:
    """候选的结论：保持。"""
    return CandidateConclusion.model_validate(_common(signal) | {"decision": Decision.KEEP})


def ddd(signal: Signal = SIGNAL) -> PoolAddition:
    """寻股加入 DDD：理由"放量突破，论点草稿：订单回暖"。"""
    return PoolAddition(symbol="DDD", reason="放量突破，论点草稿：订单回暖", signal=signal)


def eee(signal: Signal = SIGNAL) -> PoolAddition:
    """寻股加入 EEE：理由"盈利预期上调"。"""
    return PoolAddition(symbol="EEE", reason="盈利预期上调", signal=signal)


def exploration(*additions: PoolAddition) -> ExplorationConclusion:
    """寻股的结论（没有自己的信号）：按顺序加入 additions，不删除。"""
    return ExplorationConclusion.model_validate(_COMMON | {"additions": additions})
