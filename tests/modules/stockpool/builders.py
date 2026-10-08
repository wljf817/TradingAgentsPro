"""股票池测试的数据：stock-pool spec「股票池的记录」里的基准数据。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tests.fakes.stock_pool import InMemoryStockPoolStore
from tradingagentspro.contracts.listing import Exchange, Listing, SecurityType
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import (
    Catalyst,
    Comparison,
    Condition,
    ConditionKind,
    WatchThesis,
)
from tradingagentspro.modules.stockpool import (
    PoolEntry,
    PoolSource,
    StockPoolConfig,
    StockPoolService,
)

NYSE = sample_nyse_calendar()


def at(hour: int, minute: int = 0, second: int = 0, *, day: int = 14, month: int = 10) -> datetime:
    """2026 年某一天的某个 UTC 时刻，默认是基准交易日 2026-10-14。"""
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


BASELINE_NOW = at(15)  # 周三，盘中；常规时段 13:30Z 到 20:00Z

THESIS = WatchThesis(
    rationale="新品周期带动收入加速",
    entry_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(50)),
    ),
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    invalidation="毛利率连续两个季度下滑",
    review_day=date(2026, 10, 21),
)

# 三条记录都在 2026-10-14 这个交易日开始（2026-10-13 20:00Z）之前加入。
# spec 没有写的字段（BBB、CCC 的来源、理由、加入时刻和评分）在这里随意取值
AAA = PoolEntry(
    symbol="AAA",
    status=StockStatus.CANDIDATE,
    source=PoolSource.EXPLORATION,
    reason="趋势中回调，论点草稿：新品周期",
    sector="科技",
    added_at=at(15, day=13),
    expires_at=at(20, day=27),
)
BBB = PoolEntry(
    symbol="BBB",
    status=StockStatus.WATCH,
    source=PoolSource.EXPLORATION,
    reason="盈利预期上调",
    sector="医疗",
    added_at=at(15, day=5),
    expires_at=None,
    thesis=THESIS,
    score=1,
    reviewed_at=at(15, day=12),
)
CCC = PoolEntry(
    symbol="CCC",
    status=StockStatus.HOLDING,
    source=PoolSource.MANUAL,
    reason="油价上行",
    sector="能源",
    added_at=at(15, day=1),
    expires_at=None,
    score=1,
    reviewed_at=at(15, day=13),
)
BASELINE_ENTRIES = (AAA, BBB, CCC)


def listing(**changes: Any) -> Listing:
    """基准上市信息：纽交所上市的普通股，日均成交额 5,000 万美元，板块"科技"。

    changes 覆盖其中的字段。
    """
    fields: dict[str, Any] = {
        "exchange": Exchange.NYSE,
        "security_type": SecurityType.COMMON_STOCK,
        "average_dollar_volume": Decimal(50_000_000),
        "sector": "科技",
    }
    return Listing(**(fields | changes))


# "加入 DDD"的理由；"删除"的理由
ADD_REASON = "放量突破，论点草稿：订单回暖"
REMOVE_REASON = "论点不再成立"


def stock_pool_service(clock: FakeClock | None = None, **config: Any) -> StockPoolService:
    """股票池用例：测试时钟（没有传入时停在基准时刻）、纽交所样例日历、存好 AAA、BBB、CCC 三条
    记录的内存存储、默认配置。

    config 覆盖其中的配置项。
    """
    store = InMemoryStockPoolStore()
    for entry in BASELINE_ENTRIES:
        store.save(entry)
    return StockPoolService(
        store, clock or FakeClock(BASELINE_NOW), NYSE, StockPoolConfig(**config)
    )
