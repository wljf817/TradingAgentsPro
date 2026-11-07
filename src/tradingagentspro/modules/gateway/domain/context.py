"""由案件上下文和快照组出 PM 的上下文，由 Task 和快照组出子 agent 的上下文，见 shared-context spec
和 sub-agent-calls spec。纯函数，不读别的来源。
"""

from tradingagentspro.contracts.agent_call import PmRequest, SubAgentRequest
from tradingagentspro.contracts.pm_context import (
    CaseFacts,
    FilingHeadline,
    NewsHeadline,
    PmContext,
    SharedFacts,
)
from tradingagentspro.contracts.snapshot import FilingEntry, NewsEntry
from tradingagentspro.contracts.sub_agent_context import SubAgentContext


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


def sub_agent_context(request: SubAgentRequest) -> SubAgentContext:
    """这次调用子 agent 的上下文：任务、快照时刻、市场状态、这只股票的行情、事件和任务的输入。"""
    task, snapshot = request.task, request.snapshot
    inputs = {entry_id: snapshot.entry(entry_id) for entry_id in task.inputs}
    return SubAgentContext(
        task_type=task.task_type,
        symbol=task.symbol,
        question=task.question,
        taken_at=snapshot.taken_at,
        market=snapshot.market,
        quote=next((q for q in snapshot.quotes if q.symbol == task.symbol), None),
        events=snapshot.events,
        inputs=tuple(entry for entry in inputs.values() if entry is not None),
        missing_inputs=tuple(key for key, entry in inputs.items() if entry is None),
    )
