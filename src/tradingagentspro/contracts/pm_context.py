"""PM 的上下文：调用 PM 时放在对话最前面的共享事实和案件内容，转成 JSON 交给模型。

Agent 网关组出它，真实模型的后端读它。条目 ID 和数字都按快照的原样，没有的项是 null 或空列表。见
shared-context spec 和 add-shared-context 的 design D2。
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict

from tradingagentspro.contracts.agent_call import CaseContext
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.risk import Headroom
from tradingagentspro.contracts.signal import EntryId
from tradingagentspro.contracts.snapshot import (
    AccountEntry,
    EarningsEntry,
    EventEntry,
    MarketEntry,
    QuoteEntry,
    ScreenEntry,
    StockPoolEntry,
    SurprisesEntry,
)


class NewsHeadline(BaseModel):
    """一条新闻的标题。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: EntryId
    headline: str
    source: str
    published_at: AwareDatetime


class FilingHeadline(BaseModel):
    """一份公告的标题：公告没有标题，用表单类型、item 和受理时刻。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: EntryId
    form: str
    items: tuple[str, ...]
    accepted_at: AwareDatetime


class SharedFacts(BaseModel):
    """共享事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: AccountEntry
    market: MarketEntry
    pool: StockPoolEntry
    quotes: tuple[QuoteEntry, ...]


class CaseFacts(BaseModel):
    """案件内容。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_type: CaseType
    symbol: str | None
    snapshot_version: int
    taken_at: AwareDatetime
    reason: str | None
    context: CaseContext
    headroom: Headroom | None
    news: tuple[NewsHeadline, ...]
    filings: tuple[FilingHeadline, ...]
    earnings: tuple[EarningsEntry, ...]
    surprises: SurprisesEntry | None
    events: tuple[EventEntry, ...]
    screen: ScreenEntry | None


class PmContext(BaseModel):
    """PM 的上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shared: SharedFacts
    case: CaseFacts
