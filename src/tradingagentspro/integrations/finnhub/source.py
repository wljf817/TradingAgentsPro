"""Finnhub 数据源：用 Finnhub 免费档的 /stock/profile2、/calendar/earnings、/stock/earnings 实现公司
数据模块的 CompanyDataSource。

见 finnhub-company-data spec 和 add-earnings-calendar-and-sector 的 design D7、D8。
"""

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from tradingagentspro.modules.companydata import (
    CompanyDataUnavailableError,
    EarningsRelease,
    EarningsSurprise,
    ReleaseTime,
)

FINNHUB_URL = "https://finnhub.io/api/v1"

_TIMES: Final[Mapping[str, ReleaseTime]] = {
    "bmo": ReleaseTime.BEFORE_OPEN,
    "amc": ReleaseTime.AFTER_CLOSE,
    "dmh": ReleaseTime.DURING_MARKET,
}


class FinnhubConfig(BaseModel):
    """Finnhub 的配置，见 finnhub-company-data spec「Finnhub 配置」。key 由环境变量提供。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr = Field(min_length=1)
    base_url: str = Field(default=FINNHUB_URL, min_length=1)
    # 每次请求等多久没有响应就算超时
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class _Model(BaseModel):
    """Finnhub 的回复：只取用到的字段；数值按 JSON 里写的十进制精确读取。"""

    model_config = ConfigDict(frozen=True, extra="ignore")


class _Profile(_Model):
    # 空对象（查不到这只股票）和没有这个字段都是正常的回复
    finnhubIndustry: str | None = None


class _Release(_Model):
    symbol: str
    day: date = Field(alias="date")
    hour: Literal["bmo", "amc", "dmh", ""] | None
    year: int
    quarter: int = Field(ge=1, le=4)
    epsEstimate: Decimal | None
    epsActual: Decimal | None
    revenueEstimate: Decimal | None
    revenueActual: Decimal | None


class _Calendar(_Model):
    earningsCalendar: tuple[_Release, ...]


class _Surprise(_Model):
    period: date
    year: int
    quarter: int = Field(ge=1, le=4)
    actual: Decimal | None
    estimate: Decimal | None
    surprise: Decimal | None
    surprisePercent: Decimal | None


_SURPRISES = TypeAdapter(tuple[_Surprise, ...])


class FinnhubSource:
    """满足 CompanyDataSource：每次调用发一个 GET 请求，不重试，也不为了限频等待。

    key 放在请求头 X-Finnhub-Token 里，不进网址，错误信息和日志里就不会有它。调用失败按
    finnhub-company-data spec「调用失败的分类」处理：超时、连不上、429 和 5xx 抛
    CompanyDataUnavailableError；其余非 2xx 原样抛 httpx.HTTPStatusError；回复的格式对不上抛
    pydantic 的 ValidationError。
    """

    def __init__(self, config: FinnhubConfig, transport: httpx.BaseTransport | None = None) -> None:
        """transport 只给测试用：传入 httpx.MockTransport 代替真实的 Finnhub。"""
        self._http = httpx.Client(
            base_url=config.base_url,
            headers={"X-Finnhub-Token": config.api_key.get_secret_value()},
            timeout=config.timeout.total_seconds(),
            transport=transport,
        )

    def industry(self, symbol: str) -> str | None:
        """finnhubIndustry 原样；空对象、没有这个字段或者空字符串时为 None。"""
        found = _Profile.model_validate_json(self._get("/stock/profile2", {"symbol": symbol}))
        return found.finnhubIndustry or None

    def earnings_calendar(self, symbol: str, start: date, end: date) -> tuple[EarningsRelease, ...]:
        params = {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat()}
        found = _Calendar.model_validate_json(self._get("/calendar/earnings", params))
        return tuple(_release(item) for item in found.earningsCalendar)

    def earnings_surprises(self, symbol: str) -> tuple[EarningsSurprise, ...]:
        found = _SURPRISES.validate_json(self._get("/stock/earnings", {"symbol": symbol}))
        return tuple(_surprise(item) for item in found)

    def _get(self, path: str, params: Mapping[str, str]) -> bytes:
        """发一个 GET 请求。超时、连不上、429 和 5xx 抛 CompanyDataUnavailableError，其余非 2xx
        原样抛出。
        """
        try:
            response = self._http.get(path, params=params)
        except httpx.TransportError as error:  # 超时也是 TransportError
            raise CompanyDataUnavailableError(f"Finnhub GET {path}：{error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise CompanyDataUnavailableError(f"Finnhub GET {path}：{response.status_code}")
        return response.raise_for_status().content


def _release(item: _Release) -> EarningsRelease:
    return EarningsRelease(
        symbol=item.symbol,
        fiscal_year=item.year,
        fiscal_quarter=item.quarter,
        day=item.day,
        time=_TIMES.get(item.hour or ""),
        eps_estimate=item.epsEstimate,
        eps_actual=item.epsActual,
        revenue_estimate=item.revenueEstimate,
        revenue_actual=item.revenueActual,
    )


def _surprise(item: _Surprise) -> EarningsSurprise:
    return EarningsSurprise(
        period=item.period,
        fiscal_year=item.year,
        fiscal_quarter=item.quarter,
        eps_actual=item.actual,
        eps_estimate=item.estimate,
        surprise=item.surprise,
        surprise_percent=item.surprisePercent,
    )
