"""状态库快照：案件开始时由代码复制下来的、本案件 agent 要读的事实，每条事实是一个条目。

生成快照的 workflow 写它，状态库模块存它；网关、替身、案件记录和回放读它，所以放在 contracts。
条目的格式另定，不用各模块 domain 的类型：存下的快照要长期读得回来，不随模块内部的改动变化。
见 state-snapshot spec 和 add-state-snapshot 的 design D2、D4、D5。
"""

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.event import Event
from tradingagentspro.contracts.listing import Listing
from tradingagentspro.contracts.news import Filing, NewsArticle
from tradingagentspro.contracts.signal import EntryId
from tradingagentspro.contracts.stock_pool import StockStatus


class EntryKind(StrEnum):
    """条目的类别：条目 ID 里版本后面的那一段。"""

    ACCOUNT = "account"  # 账户
    MARKET = "market"  # 市场状态
    POOL = "pool"  # 股票池
    QUOTE = "quote"  # 一只股票的行情
    NEWS = "news"  # 一条新闻
    FILING = "filing"  # 一份公告
    EARNINGS = "earnings"  # 一次财报发布
    SURPRISES = "surprises"  # 一只股票的 surprise
    EVENT = "event"  # 一个事件
    SCREEN = "screen"  # 粗筛结果


_PREFIX = re.compile(r"s([1-9][0-9]*)/")


def entry_id(version: int, kind: EntryKind, key: str | None = None) -> str:
    """条目 ID：s<版本>/<类别>，有键时 s<版本>/<类别>/<键>。"""
    head = f"s{version}/{kind}"
    return head if key is None else f"{head}/{key}"


def entry_version(text: str) -> int | None:
    """条目 ID 前缀里的版本；不是 s<版本>/ 开头时为 None。"""
    match = _PREFIX.match(text)
    return None if match is None else int(match.group(1))


def check_reason(version: int, reason: str | None) -> None:
    """第 1 版没有原因，之后的版本必须有，去掉首尾空白后不为空。不符合时抛 ValueError。"""
    if version < 1:
        raise ValueError(f"快照的版本 {version} 必须从 1 开始")
    if version == 1 and reason is not None:
        raise ValueError("第 1 版快照不带原因")
    if version > 1 and (reason is None or not reason.strip()):
        raise ValueError(f"第 {version} 版快照必须写生成的原因")


class SnapshotEntry(BaseModel):
    """快照里的一个条目。entry_id 在案件的全部版本里不重复，见 spec「条目和条目 ID」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: EntryId


class HeldStock(BaseModel):
    """账户里的一只持仓。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    sector: str
    quantity: int
    average_cost: Decimal
    stop_price: Decimal


class AccountEntry(SnapshotEntry):
    """账户：执行模块的账户数据。day_pnl 是 equity 减 previous_close_equity。"""

    equity: Decimal
    previous_close_equity: Decimal
    day_pnl: Decimal
    settled_cash: Decimal
    positions: tuple[HeldStock, ...]  # 按股票代码


class MarketEntry(SnapshotEntry):
    """市场状态：生成时刻归属的交易日，以及生成时刻是否在它的常规交易时段里。"""

    trading_day: date
    in_session: bool
    session_open: AwareDatetime
    session_close: AwareDatetime


class PoolMember(BaseModel):
    """股票池里的一只股票。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    status: StockStatus
    sector: str


class StockPoolEntry(SnapshotEntry):
    """股票池。"""

    members: tuple[PoolMember, ...]  # 按股票代码


class QuoteEntry(SnapshotEntry):
    """一只股票的行情。last_price：开盘后还没有成交时为 None。"""

    symbol: str
    last_price: Decimal | None
    previous_close: Decimal
    updated_at: AwareDatetime


class NewsEntry(SnapshotEntry):
    """一条新闻，原样复制。"""

    article: NewsArticle


class FilingEntry(SnapshotEntry):
    """一份公告，原样复制。"""

    filing: Filing


class EarningsTime(StrEnum):
    """财报的发布时段，取值和公司数据模块的 ReleaseTime 相同。"""

    BEFORE_OPEN = "before_open"  # 盘前
    AFTER_CLOSE = "after_close"  # 盘后
    DURING_MARKET = "during_market"  # 盘中


class EarningsEntry(SnapshotEntry):
    """一次财报发布的最新版本。没有的项为 None。"""

    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    day: date  # 发布日，美东日期
    time: EarningsTime | None
    eps_estimate: Decimal | None
    eps_actual: Decimal | None
    revenue_estimate: Decimal | None
    revenue_actual: Decimal | None


class SurpriseQuarter(BaseModel):
    """一个季度的 EPS surprise。没有的项为 None。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: date  # 报告期
    fiscal_year: int
    fiscal_quarter: int
    eps_actual: Decimal | None
    eps_estimate: Decimal | None
    surprise: Decimal | None
    surprise_percent: Decimal | None


class SurprisesEntry(SnapshotEntry):
    """一只股票最近一次保存的 surprise；数据源没有时 quarters 为空。"""

    symbol: str
    quarters: tuple[SurpriseQuarter, ...]  # 按报告期从新到旧


class EventEntry(SnapshotEntry):
    """一个事件，原样复制；键是事件 ID。"""

    event: Event


class ScreenCondition(StrEnum):
    """粗筛的条件，取值和寻股粗筛模块的 Condition 相同。"""

    RELATIVE_STRENGTH = "relative_strength"  # 相对强度
    PULLBACK = "pullback"  # 回调
    VOLUME_SURGE = "volume_surge"  # 成交量异常


class ScreenCandidate(BaseModel):
    """粗筛名单里的一只股票。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    listing: Listing
    relative_strength: Decimal
    pullback: Decimal  # 回调幅度
    volume_ratio: Decimal  # 量比
    conditions: tuple[ScreenCondition, ...]  # 满足的条件

    @property
    def score(self) -> int:
        return len(self.conditions)


class ScreenEntry(SnapshotEntry):
    """寻股的粗筛结果。"""

    day: date  # 截止交易日
    universe: int  # 数据源给出的股票数
    eligible: int  # 合格的股票数
    trending: int  # 在上升趋势里的股票数
    candidates: tuple[ScreenCandidate, ...]  # 按名次


class Snapshot(BaseModel):
    """一个案件的一版快照，见 state-snapshot spec。没有的部分：元组为空，单个条目为 None。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    case_id: UUID
    version: int = Field(ge=1)
    case_type: CaseType
    symbol: str | None  # 寻股和每周复盘没有
    taken_at: AwareDatetime  # 生成时刻
    reason: str | None  # 生成的原因；第 1 版没有
    account: AccountEntry
    market: MarketEntry
    pool: StockPoolEntry
    quotes: tuple[QuoteEntry, ...]  # 按股票代码
    news: tuple[NewsEntry, ...]  # 按发布时刻、新闻 ID
    filings: tuple[FilingEntry, ...]  # 按受理时刻、accession number
    earnings: tuple[EarningsEntry, ...]  # 按发布日
    surprises: SurprisesEntry | None
    events: tuple[EventEntry, ...]  # 按检测时刻、事件 ID
    screen: ScreenEntry | None

    def entries(self) -> tuple[SnapshotEntry, ...]:
        """全部条目，按 spec「条目和条目 ID」的先后。"""
        surprises = () if self.surprises is None else (self.surprises,)
        screen = () if self.screen is None else (self.screen,)
        listed = (*self.quotes, *self.news, *self.filings, *self.earnings, *surprises)
        return (self.account, self.market, self.pool, *listed, *self.events, *screen)

    def entry(self, entry_id: str) -> SnapshotEntry | None:
        """这一版里 ID 是 entry_id 的条目；没有时为 None。"""
        return next((e for e in self.entries() if e.entry_id == entry_id), None)

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_reason(self.version, self.reason)
        ids = [e.entry_id for e in self.entries()]
        prefix = f"s{self.version}/"
        if len(set(ids)) != len(ids) or not all(i.startswith(prefix) for i in ids):
            raise ValueError(f"条目 ID 必须不重复，并以 {prefix} 开头")
        return self
