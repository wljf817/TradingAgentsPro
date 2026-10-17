"""寻股粗筛需要的外部接口：股票列表、日线、板块和粗筛结果的存储。"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

from tradingagentspro.modules.screener.domain.indicators import DailyBar
from tradingagentspro.modules.screener.domain.screen import ScreenRun, Security


class ScreenDataUnavailableError(RuntimeError):
    """股票列表或日线的数据源超时、断线、限频或出错：这次没有取到，以后再取。"""


class SecuritySource(Protocol):
    """股票列表的数据源。失败时抛 ScreenDataUnavailableError，不重试。"""

    def universe(self, min_market_cap: Decimal) -> Sequence[Security]:
        """美股里市值不低于 min_market_cap（美元）的股票，不重复；数据源认不出的不在里面。"""
        ...

    def security(self, symbol: str) -> Security | None:
        """一只股票的交易所和证券类型；数据源认不出时为 None。"""
        ...


class BarSource(Protocol):
    """日线的数据源。symbols 非空、不重复，由模块保证。失败时抛 ScreenDataUnavailableError。"""

    def daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> Mapping[str, Sequence[DailyBar]]:
        """start 到 end（含两端）的日线，按交易日从早到晚；没有日线的股票不在结果里。"""
        ...


class SectorSource(Protocol):
    """板块的来源。装配时交进公司数据模块的 CompanyDataService，它的错误原样传出。"""

    def sector(self, symbol: str) -> str | None:
        """板块 ETF 的代码；没有板块时为 None。"""
        ...


class ScreenStore(Protocol):
    """粗筛结果的存储，按截止交易日只追加，数据库实现在 repo.py。错误原样抛出，不当作没有结果。"""

    def save(self, run: ScreenRun) -> None:
        """内容和这一天的最新版本不同时写入新版本。"""
        ...

    def get(self, day: date) -> ScreenRun | None:
        """这个截止交易日最新的粗筛结果；没有时为 None。"""
        ...
