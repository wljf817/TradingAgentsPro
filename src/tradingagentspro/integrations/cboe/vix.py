"""Cboe VIX 源：读 Cboe 的 VIX 历史文件，实现行情模块的 VixSource。

见 market-context spec「Cboe 数据源」和 add-market-context 的 design D1。文件不需要 key，每天一行，
当天的收盘值要到收盘后几小时才有。
"""

import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.snapshot import VixClose
from tradingagentspro.modules.marketdata import VixUnavailableError

CBOE_VIX_URL: Final = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
_HEADER: Final = ["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]


class CboeConfig(BaseModel):
    """Cboe 的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每次请求等多久没有响应就算超时
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class CboeVixSource:
    """满足 VixSource：每次调用读一次文件。"""

    def __init__(self, config: CboeConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(timeout=config.timeout.total_seconds(), transport=transport)

    def closes(self) -> tuple[VixClose, ...]:
        """文件里的全部收盘值，按文件的顺序。读不到或格式不对时抛 VixUnavailableError。"""
        try:
            response = self._http.get(CBOE_VIX_URL)
        except httpx.TransportError as error:  # 超时也是 TransportError
            raise VixUnavailableError(f"Cboe VIX 文件：{error}") from error
        if response.status_code != httpx.codes.OK:
            raise VixUnavailableError(f"Cboe VIX 文件：{response.status_code}")
        return _closes(response.text)


def _closes(text: str) -> tuple[VixClose, ...]:
    reader = csv.reader(StringIO(text))
    if next(reader, None) != _HEADER:
        raise VixUnavailableError("Cboe VIX 文件的表头不对")
    closes = tuple(_close(row) for row in reader)
    if not closes:
        raise VixUnavailableError("Cboe VIX 文件没有数据行")
    return closes


def _close(row: list[str]) -> VixClose:
    try:
        month, day, year = row[0].split("/")
        return VixClose(day=date(int(year), int(month), int(day)), close=Decimal(row[4]))
    except (IndexError, ValueError, InvalidOperation) as error:
        raise VixUnavailableError(f"Cboe VIX 文件的这一行读不出来：{row}") from error
