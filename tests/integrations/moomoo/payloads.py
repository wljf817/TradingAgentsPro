"""2026-10-06 从真实 OpenD 读到的市场快照（美东夏令时，当时是盘后）。

见 moomoo-quotes spec 的基准数据。

SDK 返回的 DataFrame 有上百个字段，这里只保留映射用到的字段、盘前盘后夜盘的价格和几个常见字段。
数值是 Python 的 float，组成 DataFrame 以后和 SDK 一样是 numpy 的 float64；update_time 是字符串。
"""

from typing import Any

import pandas as pd

AAPL: dict[str, Any] = {
    "code": "US.AAPL",
    "name": "Apple",
    "update_time": "2026-10-06 17:53:59.407",
    "last_price": 333.63,
    "open_price": 332.28,
    "prev_close_price": 332.89,
    "volume": 30421856.0,
    "suspension": False,
    "sec_status": "NORMAL",
    "pre_price": 332.01,
    "after_price": 333.85,
    "overnight_price": 333.02,
}

SPY: dict[str, Any] = {
    "code": "US.SPY",
    "name": "SPDR S&P 500 ETF",
    "update_time": "2026-10-06 17:53:52.101",
    "last_price": 779.09,
    "open_price": 778.15,
    "prev_close_price": 774.83,
    "volume": 35734306.0,
    "suspension": False,
    "sec_status": "NORMAL",
    "pre_price": 778.15,
    "after_price": 779.74,
    "overnight_price": 776.08,
}

BRK_B: dict[str, Any] = {
    "code": "US.BRK.B",
    "name": "Berkshire Hathaway-B",
    "update_time": "2026-10-06 17:53:22.816",
    "last_price": 505.54,
    "open_price": 504.335,
    "prev_close_price": 504.26,
    "volume": 3246092.0,
    "suspension": False,
    "sec_status": "NORMAL",
    "pre_price": 504.39,
    "after_price": 505.56,
    "overnight_price": 505.11,
}

# OpenD 查不到的代码：不报错，这一行 update_time 为空、价格全为 0
ZZZZQQ: dict[str, Any] = {
    "code": "US.ZZZZQQ",
    "name": "Unknown stock.",
    "update_time": "",
    "last_price": 0.0,
    "open_price": 0.0,
    "prev_close_price": 0.0,
    "volume": 0.0,
    "suspension": False,
    "sec_status": "N/A",
    "pre_price": "N/A",
    "after_price": "N/A",
    "overnight_price": "N/A",
}


def row(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """基准的一行，覆盖给出的字段。"""
    return base | changes


def snapshot(*rows: dict[str, Any]) -> Any:
    """按给出的顺序组成快照，和 SDK 的 get_market_snapshot 返回的 DataFrame 一样。"""
    return pd.DataFrame(list(rows))
