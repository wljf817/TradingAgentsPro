"""moomoo 股票列表：用 OpenD 的条件选股和股票静态信息实现寻股粗筛模块的 SecuritySource。

见 moomoo-securities spec 和 add-stock-screen 的 design D10。
"""

import threading
from collections.abc import Callable, Iterable
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final, Protocol

from moomoo import RET_OK, Market, SimpleFilter, SortDir, StockField
from moomoo import SecurityType as MoomooSecurityType
from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.listing import Exchange, SecurityType
from tradingagentspro.modules.screener import ScreenDataUnavailableError, Security

# OpenD 的美股代码是股票代码加上这个前缀，比如 US.AAPL
_US_PREFIX: Final = "US."
# 条件选股每页最多 200 只；静态信息每次也查 200 个代码
_PAGE_SIZE: Final = 200
_EXCHANGES: Final = {"US_NYSE": Exchange.NYSE, "US_NASDAQ": Exchange.NASDAQ}
_SECURITY_TYPES: Final = {"STOCK": SecurityType.COMMON_STOCK, "ETF": SecurityType.ETF}


class MoomooSecuritiesConfig(BaseModel):
    """moomoo 股票列表的配置，见 moomoo-securities spec「moomoo 股票列表配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 条件选股每 30 秒最多 10 次；每个请求之后等这么久
    request_interval: timedelta = Field(default=timedelta(seconds=3), gt=timedelta(0))


class SecuritiesContext(Protocol):
    """适配器用到的 OpenD 行情连接的那部分：moomoo 的 OpenQuoteContext 满足它。

    返回 (RET_OK, 结果)；失败时返回 (其他值, 错误信息)。
    """

    def get_stock_filter(
        self, market: str, filter_list: list[Any], begin: int, num: int
    ) -> tuple[int, Any]: ...

    def get_stock_basicinfo(
        self, market: str, stock_type: str, code_list: list[str]
    ) -> tuple[int, Any]: ...


def _wait(duration: timedelta) -> None:
    """阻塞等待这么久；time 模块全项目禁用。"""
    threading.Event().wait(duration.total_seconds())


class MoomooSecuritySource:
    """满足寻股粗筛模块的 SecuritySource：每个请求之后等待请求间隔，失败时报不可用，不重试。"""

    def __init__(
        self,
        context: SecuritiesContext,
        config: MoomooSecuritiesConfig,
        pause: Callable[[timedelta], None] = _wait,
    ) -> None:
        """pause 只给测试用：记下等待而不真的等。"""
        self._context = context
        self._config = config
        self._pause = pause

    def universe(self, min_market_cap: Decimal) -> tuple[Security, ...]:
        """条件选股取完市值不低于下限的全部分页，再分批查静态信息。"""
        codes = list(dict.fromkeys(self._filtered(min_market_cap)))
        securities: list[Security] = []
        for start in range(0, len(codes), _PAGE_SIZE):
            securities.extend(self._static(codes[start : start + _PAGE_SIZE]))
        return tuple(securities)

    def security(self, symbol: str) -> Security | None:
        return next(iter(self._static([_US_PREFIX + symbol])), None)

    def _filtered(self, min_market_cap: Decimal) -> Iterable[str]:
        """条件选股按市值从高到低给出的代码，翻页直到最后一页。"""
        market_cap = SimpleFilter()
        market_cap.stock_field = StockField.MARKET_VAL
        market_cap.filter_min = float(min_market_cap)
        market_cap.is_no_filter = False
        market_cap.sort = SortDir.DESCEND
        begin = 0
        while True:
            last_page, _, items = self._checked(
                "条件选股",
                self._context.get_stock_filter(
                    Market.US, [market_cap], begin=begin, num=_PAGE_SIZE
                ),
            )
            yield from (item.stock_code for item in items)
            if last_page:
                return
            begin += _PAGE_SIZE

    def _static(self, codes: list[str]) -> list[Security]:
        """这些代码的静态信息，按给出的顺序；OpenD 认不出的（没有这一行或 delisting）不在里面。"""
        data = self._checked(
            "静态信息",
            self._context.get_stock_basicinfo(Market.US, MoomooSecurityType.STOCK, code_list=codes),
        )
        rows = {row["code"]: row for row in data.to_dict("records") if row["delisting"] is not True}
        return [_security(rows[code]) for code in codes if code in rows]

    def _checked(self, name: str, result: tuple[int, Any]) -> Any:
        """一个请求刚刚结束：等待请求间隔，失败时抛 ScreenDataUnavailableError。"""
        ret, data = result
        self._pause(self._config.request_interval)
        if ret != RET_OK:
            raise ScreenDataUnavailableError(f"OpenD {name}：{data}")
        return data


def _security(row: dict[str, Any]) -> Security:
    return Security(
        symbol=row["code"].removeprefix(_US_PREFIX),
        exchange=_EXCHANGES.get(row["exchange_type"], Exchange.OTHER),
        security_type=_SECURITY_TYPES.get(row["stock_type"], SecurityType.OTHER),
    )
