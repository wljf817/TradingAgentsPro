"""上市信息：加入股票池时由调用方从数据源查好传入，见 stock-pool spec「加入候选」。

枚举只列 Proposal 和人的决定里提到的值，其余归"其他"。允许交易的范围由股票池配置。
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Exchange(StrEnum):
    NYSE = "nyse"
    NASDAQ = "nasdaq"
    OTHER = "other"  # 其他交易所，包括 OTC


class SecurityType(StrEnum):
    COMMON_STOCK = "common_stock"
    ADR = "adr"
    ETF = "etf"
    LEVERAGED = "leveraged"  # 杠杆和反向产品，包括杠杆 ETF
    OTHER = "other"


class Listing(BaseModel):
    """一只股票的上市信息，由调用方从数据源查好传入；数据来自数据源，不加约束。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exchange: Exchange
    security_type: SecurityType
    average_dollar_volume: Decimal  # 日均成交额，美元；按多少天平均由 M4 定
    sector: str
