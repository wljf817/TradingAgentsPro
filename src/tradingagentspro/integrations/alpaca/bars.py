"""Alpaca 日线：用 Alpaca 市场数据 API 的历史 K 线接口实现寻股粗筛模块的 BarSource。

见 alpaca-bars spec 和 add-stock-screen 的 design D11。
"""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from tradingagentspro.integrations.alpaca.news import DATA_URL
from tradingagentspro.modules.screener import DailyBar, ScreenDataUnavailableError

# 日线的时间戳是美东当天零点；按这个时区换成交易日，不依赖本机时区
NEW_YORK: Final = ZoneInfo("America/New_York")
# 每个请求最多这么多只股票；每页最多 10,000 根（按全部股票合计），是 Alpaca 允许的上限
_SYMBOLS_PER_REQUEST: Final = 100
_PAGE_SIZE: Final = 10_000
_PATH: Final = "/v2/stocks/bars"


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


class AlpacaBarSource:
    """满足 BarSource：按拆股调整的日线，每 100 只股票一批，取完全部分页。

    调用失败按 alpaca-bars spec「日线请求失败的分类」处理：超时、连不上、429 和 5xx 抛
    ScreenDataUnavailableError；其余的 4xx 原样抛 httpx.HTTPStatusError；回复读不出来抛 pydantic 的
    ValidationError。任何一批、一页失败整次失败，不重试。
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
                page = _Page.model_validate_json(self._get(params, token).content)
                for symbol, bars in page.bars.items():
                    found.setdefault(symbol, []).extend(
                        b for b in map(_daily_bar, bars) if start <= b.day <= end
                    )
                token = page.next_page_token
                if token is None:
                    break
        return {symbol: tuple(bars) for symbol, bars in found.items() if bars}

    def _get(self, params: dict[str, str], token: str | None) -> httpx.Response:
        """请求一页。超时、连不上、429 和 5xx 抛 ScreenDataUnavailableError，其余 4xx 原样抛出。"""
        query = params if token is None else {**params, "page_token": token}
        try:
            response = self._http.get(_PATH, params=query)
        except httpx.TransportError as error:  # 超时也是 TransportError
            raise ScreenDataUnavailableError(f"Alpaca 日线 GET {_PATH}：{error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise ScreenDataUnavailableError(f"Alpaca 日线 GET {_PATH}：{response.status_code}")
        return response.raise_for_status()


def _daily_bar(bar: _Bar) -> DailyBar:
    return DailyBar(day=bar.t.astimezone(NEW_YORK).date(), close=bar.c, vwap=bar.vw, volume=bar.v)
