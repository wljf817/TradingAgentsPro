"""由案件上下文和快照组出 PM 的上下文，见 shared-context spec。纯函数，不读别的来源。"""

from tradingagentspro.contracts.agent_call import PmRequest
from tradingagentspro.contracts.pm_context import (
    CaseFacts,
    FilingHeadline,
    NewsHeadline,
    PmContext,
    SharedFacts,
)
from tradingagentspro.contracts.snapshot import FilingEntry, NewsEntry


def _news(entry: NewsEntry) -> NewsHeadline:
    article = entry.article
    return NewsHeadline(
        entry_id=entry.entry_id,
        headline=article.headline,
        source=article.source,
        published_at=article.published_at,
    )


def _filing(entry: FilingEntry) -> FilingHeadline:
    filing = entry.filing
    return FilingHeadline(
        entry_id=entry.entry_id,
        form=filing.form,
        items=filing.items,
        accepted_at=filing.accepted_at,
    )


def pm_context(request: PmRequest) -> PmContext:
    """这次调用 PM 的上下文：快照的全部条目，新闻和公告换成标题；不含宪章和对话。"""
    snapshot = request.snapshot
    return PmContext(
        shared=SharedFacts(
            account=snapshot.account,
            market=snapshot.market,
            pool=snapshot.pool,
            quotes=snapshot.quotes,
        ),
        case=CaseFacts(
            case_type=snapshot.case_type,
            symbol=snapshot.symbol,
            snapshot_version=snapshot.version,
            taken_at=snapshot.taken_at,
            reason=snapshot.reason,
            context=request.context,
            headroom=snapshot.headroom,
            news=tuple(_news(entry) for entry in snapshot.news),
            filings=tuple(_filing(entry) for entry in snapshot.filings),
            earnings=snapshot.earnings,
            surprises=snapshot.surprises,
            events=snapshot.events,
            screen=snapshot.screen,
        ),
    )
