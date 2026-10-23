"""moomoo 行情源：用 OpenD 的市场快照实现行情模块的 QuoteSource。

见 moomoo-quotes spec 和 add-moomoo-quotes 的 design D4 到 D6、D9。
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from moomoo import RET_OK, OpenQuoteContext, SysConfig
from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.modules.marketdata import Quote, QuoteUnavailableError

# 快照的 update_time 是美东时间；按这个时区解读，不依赖本机时区
NEW_YORK = ZoneInfo("America/New_York")

# OpenD 的美股代码是股票代码加上这个前缀，比如 US.AAPL
_US_PREFIX = "US."

# 开盘后 last_price 可能还停在前一交易日收盘价上，这么久以内不给最新价，见 design D5
_SETTLING = timedelta(minutes=1)


class MoomooConfig(BaseModel):
    """连接 OpenD 的配置，含义见 moomoo-quotes spec「连接配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=11111, ge=1, le=65535)
    # 没有连上 OpenD 时，一次取行情最多等这么久
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class SnapshotContext(Protocol):
    """适配器用到的 OpenD 行情连接的那部分：moomoo 的 OpenQuoteContext 满足它。

    返回 (RET_OK, 快照的 DataFrame)；失败时返回 (其他值, 错误信息)。
    """

    def get_market_snapshot(self, code_list: list[str]) -> tuple[int, Any]: ...


class MoomooQuoteSource:
    """满足行情模块的 QuoteSource：每次调用发一次快照请求，不订阅，不重试。"""

    def __init__(self, context: SnapshotContext) -> None:
        self._context = context

    def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        codes = {_US_PREFIX + symbol: symbol for symbol in symbols}
        ret, data = self._context.get_market_snapshot(list(codes))
        if ret != RET_OK:
            raise QuoteUnavailableError(f"OpenD 快照：{data}")
        quotes: dict[str, Quote] = {}
        for row in data.to_dict("records"):
            symbol = codes.get(row["code"])  # 不在请求里的代码忽略
            quote = None if symbol is None else _quote(symbol, row)
            if quote is not None:
                quotes[quote.symbol] = quote
        return quotes


def open_quote_context(config: MoomooConfig) -> OpenQuoteContext:
    """建立 OpenD 行情连接：不等连上就返回，之后 SDK 在后台重连。调用方用完以后 close()。

    没连上时一次调用最多等配置的超时，然后返回错误。SDK 的线程设为 daemon 线程，
    忘了关闭时进程也能退出。见 design D6。
    """
    SysConfig.set_all_thread_daemon(True)
    context = OpenQuoteContext(host=config.host, port=config.port, is_async_connect=True)
    context.set_sync_query_connect_timeout(config.timeout.total_seconds())
    return context


def _quote(symbol: str, row: Mapping[str, Any]) -> Quote | None:
    """快照的一行；update_time 为空（OpenD 查不到这只股票，价格全为 0）时为 None。

    按开盘价和更新时刻判断快照处在哪一段，见 design D5、D9：开盘前还没切换到当天时，常规时段的
    字段都是上一个交易日的，last_price 就是前一交易日收盘价；有了开盘价、更新到开盘 1 分钟以后
    才给最新价和量比。
    """
    update_time = row["update_time"]
    if not update_time:
        return None
    updated = datetime.fromisoformat(update_time).replace(tzinfo=NEW_YORK)
    opening = updated.replace(hour=9, minute=30, second=0, microsecond=0)
    opened = row["open_price"] != 0
    stale = opened and updated < opening
    priced = opened and updated >= opening + _SETTLING
    return Quote(
        symbol=symbol,
        last_price=_price(row["last_price"]) if priced else None,
        previous_close=_price(row["last_price"] if stale else row["prev_close_price"]),
        volume_ratio=_price(row["volume_ratio"]) if priced else None,
        updated_at=updated.astimezone(UTC),
    )


def _price(value: Any) -> Decimal:
    """价格和量比：numpy 的 float64 先转成 float，再取最短的十进制写法：333.63 得到
    Decimal("333.63")。
    """
    return Decimal(repr(float(value)))
