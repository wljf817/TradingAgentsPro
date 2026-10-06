"""行情模块需要的外部接口：行情源。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

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
