"""Alpaca 日线：用 Alpaca 市场数据 API 的历史 K 线接口实现寻股粗筛模块的 BarSource，以及公司数据
模块的 PriceSource（原始收盘价）；用公司行动接口实现公司数据模块的 SplitSource（拆股记录）。

见 alpaca-bars spec、add-stock-screen 的 design D11 和 add-valuation-tool 的 design D2。
"""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from tradingagentspro.integrations.alpaca.news import DATA_URL
from tradingagentspro.modules.companydata import CompanyDataUnavailableError, DailyClose, Split
from tradingagentspro.modules.screener import DailyBar, ScreenDataUnavailableError

# 日线的时间戳是美东当天零点；按这个时区换成交易日，不依赖本机时区
NEW_YORK: Final = ZoneInfo("America/New_York")
# 每个请求最多这么多只股票；每页最多 10,000 根（按全部股票合计），是 Alpaca 允许的上限
_SYMBOLS_PER_REQUEST: Final = 100
_PAGE_SIZE: Final = 10_000
_PATH: Final = "/v2/stocks/bars"
_ACTIONS_PATH: Final = "/v1/corporate-actions"
# 拆股记录从这一天起取：早于 Alpaca 最早的拆股记录（2008 年前后），见 alpaca-bars spec「拆股记录」
_SPLITS_FROM: Final = "2000-01-01"
_ACTIONS_PAGE_SIZE: Final = 1000


class AlpacaBarsConfig(BaseModel):
    """Alpaca 日线的配置，见 alpaca-bars spec「Alpaca 日线配置」。key 和模拟盘用同一组。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    secret_key: SecretStr
    base_url: str = DATA_URL
    # 每次请求等多久没有响应就算超时
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class _Bar(BaseModel):
    """Alpaca 的一根日线：只取用到的字段。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    t: AwareDatetime
    c: Decimal
    v: int
    vw: Decimal


class _Page(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    bars: dict[str, tuple[_Bar, ...]]
    next_page_token: str | None


class _RawBar(BaseModel):
    """不调整的一根日线：只取交易日和收盘价。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    t: AwareDatetime
    c: Decimal


class _RawPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    bars: dict[str, tuple[_RawBar, ...]]
    next_page_token: str | None


class _SplitAction(BaseModel):
    """公司行动接口的一次拆股：只取除权日和新旧比例。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ex_date: date
    old_rate: Decimal
    new_rate: Decimal


class _Actions(BaseModel):
    """只有请求的种类里有记录的才出现，没有记录时是空对象。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    forward_splits: tuple[_SplitAction, ...] = ()
    reverse_splits: tuple[_SplitAction, ...] = ()


class _ActionsPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    corporate_actions: _Actions
    next_page_token: str | None


class AlpacaBarSource:
    """满足 BarSource：按拆股调整的日线，每 100 只股票一批，取完全部分页；也满足 PriceSource 和
    SplitSource：一只股票的原始收盘价和拆股记录。

    调用失败按 alpaca-bars spec「日线请求失败的分类」处理：超时、连不上、429 和 5xx 时，日线抛
    ScreenDataUnavailableError，原始收盘价和拆股记录抛 CompanyDataUnavailableError；其余的 4xx
    原样抛 httpx.HTTPStatusError；回复读不出来抛 pydantic 的 ValidationError。任何一批、一页失败
    整次失败，不重试。
    """

    def __init__(
        self, config: AlpacaBarsConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        """transport 只给测试用：传入 httpx.MockTransport 代替真实的 Alpaca。"""
        self._http = httpx.Client(
            base_url=config.base_url,
            headers={
                "APCA-API-KEY-ID": config.key_id,
                "APCA-API-SECRET-KEY": config.secret_key.get_secret_value(),
            },
            timeout=config.timeout.total_seconds(),
            transport=transport,
        )

    def daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, tuple[DailyBar, ...]]:
        """start 到 end（含两端）的日线。请求不带 end：免费档取不了最近 15 分钟，Alpaca 自己取到它
        允许的最新时刻；end 以后的日线回来以后按美东日期去掉。
        """
        found: dict[str, list[DailyBar]] = {}
        for first in range(0, len(symbols), _SYMBOLS_PER_REQUEST):
            batch = symbols[first : first + _SYMBOLS_PER_REQUEST]
            params = {
                "symbols": ",".join(batch),
                "timeframe": "1Day",
                "start": start.isoformat(),
                "adjustment": "split",
                "feed": "sip",
                "limit": str(_PAGE_SIZE),
                "sort": "asc",
            }
            token: str | None = None
            while True:
                response = self._get(_PATH, params, token, ScreenDataUnavailableError)
                page = _Page.model_validate_json(response.content)
                for symbol, bars in page.bars.items():
                    found.setdefault(symbol, []).extend(
                        b for b in map(_daily_bar, bars) if start <= b.day <= end
                    )
                token = page.next_page_token
                if token is None:
                    break
        return {symbol: tuple(bars) for symbol, bars in found.items() if bars}

    def daily_closes(self, symbol: str, start: date, end: date) -> tuple[DailyClose, ...]:
        """start 到 end（含两端）的原始收盘价，见 alpaca-bars spec「原始收盘价」。请求不带 end，
        理由同 daily_bars。
        """
        params = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start.isoformat(),
            "adjustment": "raw",
            "feed": "sip",
            "limit": str(_PAGE_SIZE),
            "sort": "asc",
        }
        closes: list[DailyClose] = []
        token: str | None = None
        while True:
            response = self._get(_PATH, params, token, CompanyDataUnavailableError)
            page = _RawPage.model_validate_json(response.content)
            closes.extend(
                DailyClose(day, bar.c)
                for bar in page.bars.get(symbol, ())
                if start <= (day := bar.t.astimezone(NEW_YORK).date()) <= end
            )
            token = page.next_page_token
            if token is None:
                return tuple(closes)

    def splits(self, symbol: str, end: date) -> tuple[Split, ...]:
        """除权日不晚于 end 的拆股，见 alpaca-bars spec「拆股记录」。"""
        params = {
            "symbols": symbol,
            "types": "forward_split,reverse_split",
            "start": _SPLITS_FROM,
            "end": end.isoformat(),
            "limit": str(_ACTIONS_PAGE_SIZE),
        }
        found: list[Split] = []
        token: str | None = None
        while True:
            response = self._get(_ACTIONS_PATH, params, token, CompanyDataUnavailableError)
            actions = _ActionsPage.model_validate_json(response.content)
            found.extend(
                Split(action.ex_date, action.new_rate / action.old_rate)
                for action in (
                    *actions.corporate_actions.forward_splits,
                    *actions.corporate_actions.reverse_splits,
                )
                if action.ex_date <= end
            )
            token = actions.next_page_token
            if token is None:
                return tuple(sorted(found, key=lambda split: split.ex_date))

    def _get(
        self,
        path: str,
        params: dict[str, str],
        token: str | None,
        unavailable: type[RuntimeError],
    ) -> httpx.Response:
        """请求一页。超时、连不上、429 和 5xx 抛 unavailable，其余 4xx 原样抛出。"""
        query = params if token is None else {**params, "page_token": token}
        try:
            response = self._http.get(path, params=query)
        except httpx.TransportError as error:  # 超时也是 TransportError
            raise unavailable(f"Alpaca GET {path}：{error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise unavailable(f"Alpaca GET {path}：{response.status_code}")
        return response.raise_for_status()


def _daily_bar(bar: _Bar) -> DailyBar:
    return DailyBar(day=bar.t.astimezone(NEW_YORK).date(), close=bar.c, vwap=bar.vw, volume=bar.v)
