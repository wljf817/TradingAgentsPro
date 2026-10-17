"""粗筛的指标：日均成交额、相对强度、上升趋势、回调幅度、量比，见 stock-screen spec。

除了日均成交额，函数接收的都是窗口里一天一根的日线，最后一根是截止交易日；位置就是交易日，倒数第
k + 1 根是 k 个交易日以前。全部用 Decimal 计算，不四舍五入。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tradingagentspro.modules.screener.config import ScreenerConfig


@dataclass(frozen=True, slots=True)
class DailyBar:
    """一只股票一个交易日的日线，按拆股调整。数据来自数据源，不加约束。"""

    day: date  # 美东日期
    close: Decimal
    vwap: Decimal  # 成交均价
    volume: int


def average_dollar_volume(bars: Iterable[DailyBar], days: Sequence[date]) -> Decimal:
    """days 这几个交易日每天的成交额（成交均价乘成交量）的平均；没有日线的那天算 0，也计入天数。"""
    wanted = set(days)
    total = sum((b.vwap * b.volume for b in bars if b.day in wanted), Decimal(0))
    return total / len(days)


def relative_strength(
    bars: Sequence[DailyBar], benchmark: Sequence[DailyBar], config: ScreenerConfig
) -> Decimal:
    """收盘价之比除以基准在同两天的收盘价之比再减 1，两天是截止交易日和它之前那么多个交易日。"""
    k = config.relative_strength_sessions
    stock = bars[-1].close / bars[-1 - k].close
    market = benchmark[-1].close / benchmark[-1 - k].close
    return stock / market - 1


def uptrend(bars: Sequence[DailyBar], config: ScreenerConfig) -> bool:
    """短均线高于长均线，并且截止交易日的收盘价高于长均线。"""
    closes = [b.close for b in bars]
    long = _average(closes[-config.long_ma_sessions :])
    short = _average(closes[-config.short_ma_sessions :])
    return short > long and closes[-1] > long


def pullback(bars: Sequence[DailyBar], config: ScreenerConfig) -> Decimal:
    """1 减去截止交易日的收盘价除以最近那么多个交易日里最高的收盘价。"""
    closes = [b.close for b in bars[-config.pullback_sessions :]]
    return 1 - closes[-1] / max(closes)


def volume_ratio(bars: Sequence[DailyBar], config: ScreenerConfig) -> Decimal:
    """近期的平均成交量除以再之前基期的平均成交量。"""
    recent = config.volume_recent_sessions
    volumes = [Decimal(b.volume) for b in bars]
    base = volumes[-recent - config.volume_base_sessions : -recent]
    return _average(volumes[-recent:]) / _average(base)


def _average(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)
