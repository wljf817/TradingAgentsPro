"""寻股粗筛：由代码从更大的股票范围里找出上升趋势里的股票，按相对强度、回调、成交量异常粗筛出前
N 只，存进状态库给寻股案件；并为一只股票组装加入股票池要用的上市信息。

规则见 openspec 的 stock-screen spec。数据源、板块和存储由 bootstrap 注入；粗筛结果存在状态库的
screener_runs 表里，只由这个模块写入，数据库实现是 PostgresScreenStore。模块之外的代码只能 import
这里导出的名字。
"""

from tradingagentspro.modules.screener.config import ScreenerConfig
from tradingagentspro.modules.screener.domain.indicators import (
    DailyBar,
    average_dollar_volume,
    pullback,
    relative_strength,
    uptrend,
    volume_ratio,
)
from tradingagentspro.modules.screener.domain.screen import (
    Candidate,
    Condition,
    Evaluation,
    Metrics,
    Ranked,
    Scope,
    ScreenRun,
    Security,
    cutoff,
    evaluate,
    history_sessions,
    sessions,
)
from tradingagentspro.modules.screener.ports import (
    BarSource,
    ScreenDataUnavailableError,
    ScreenStore,
    SectorSource,
    SecuritySource,
)
from tradingagentspro.modules.screener.repo import PostgresScreenStore
from tradingagentspro.modules.screener.service import ScreenerService

__all__ = [
    "BarSource",
    "Candidate",
    "Condition",
    "DailyBar",
    "Evaluation",
    "Metrics",
    "PostgresScreenStore",
    "Ranked",
    "Scope",
    "ScreenDataUnavailableError",
    "ScreenRun",
    "ScreenStore",
    "ScreenerConfig",
    "ScreenerService",
    "SectorSource",
    "Security",
    "SecuritySource",
    "average_dollar_volume",
    "cutoff",
    "evaluate",
    "history_sessions",
    "pullback",
    "relative_strength",
    "sessions",
    "uptrend",
    "volume_ratio",
]
