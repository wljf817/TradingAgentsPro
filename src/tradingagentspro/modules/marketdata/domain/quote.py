"""一只股票的行情，见 market-data spec「取行情」。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Quote:
    """一只股票的行情。数据来自行情源，不加约束。

    - last_price：当前交易日常规交易时段的最新成交价；开盘后还没有成交时为 None，
      不拿前一天的收盘价充当今天的价格，见 add-moomoo-quotes 的 design D2
    - previous_close：前一交易日常规交易时段的收盘价
    - updated_at：行情源最近一次更新这只股票行情的时刻，UTC
    """

    symbol: str
    last_price: Decimal | None
    previous_close: Decimal
    updated_at: datetime
