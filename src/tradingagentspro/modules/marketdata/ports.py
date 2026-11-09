"""行情模块需要的外部接口：行情源、VIX 源和 K 线源。"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol

from tradingagentspro.contracts.snapshot import VixClose
from tradingagentspro.modules.marketdata.domain.bars import SourceBar, SourceDayBar, Split
from tradingagentspro.modules.marketdata.domain.quote import Quote


class QuoteUnavailableError(RuntimeError):
    """行情源超时、断线、限频或出错：这次没有取到行情。"""


class QuoteSource(Protocol):
    """行情源。

    symbols 非空、不重复，由行情模块保证。结果按股票代码对应；行情源没有数据的股票不在结果里。
    失败时抛 QuoteUnavailableError，不重试。
    """

    def quotes(self, symbols: Sequence[str]) -> Mapping[str, Quote]:
        """这些股票的行情。"""
        ...


class VixUnavailableError(RuntimeError):
    """VIX 源超时、断线、出错或格式不对，或者没有早于要取的交易日的收盘值。"""


class VixSource(Protocol):
    """VIX 源。失败时抛 VixUnavailableError，不重试。"""

    def closes(self) -> Sequence[VixClose]:
        """全部收盘值。"""
        ...


class BarsUnavailableError(RuntimeError):
    """K 线源超时、断线、限频或出错：这次没有取到 K 线或拆股记录。"""


class BarHistorySource(Protocol):
    """K 线源：不调整的原始 K 线和拆股记录，见 price-bars spec「取数的请求」。

    结束时刻由行情模块保证早于现在至少 15 分钟。结果按股票代码对应，每只按时间从早到晚排列；
    没有 K 线的股票不在结果里。失败时抛 BarsUnavailableError，不重试。
    """

    def quarter_hours(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> Mapping[str, tuple[SourceBar, ...]]:
        """开始时刻不早于 start、不晚于 end 的原始 15 分钟线，含盘前盘后。"""
        ...

    def days(
        self, symbols: Sequence[str], start: date, end: datetime
    ) -> Mapping[str, tuple[SourceDayBar, ...]]:
        """交易日不早于 start、零点不晚于 end 的原始日线；end 当天的是整天的。"""
        ...

    def splits(self, symbol: str, end: date) -> tuple[Split, ...]:
        """除权日不晚于 end 的拆股，按除权日从早到晚。"""
        ...
