"""股票状态：调度器、收盘检查和跟进审批按它判断，见 stock-pool spec。"""

from enum import StrEnum


class StockStatus(StrEnum):
    """股票在股票池里的状态；允许的结论只由它决定。

    移出不是一种状态：移出的股票不在池子里，不会有案件。
    """

    CANDIDATE = "candidate"
    WATCH = "watch"
    HOLDING = "holding"
