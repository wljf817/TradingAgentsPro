"""公告的初筛：event-rules spec「公告的初筛」。8-K 的 item 2.02 是财报发布，见 earnings.py。

表单按 SEC 的写法完全相同才算，分级在配置的 FilingTiers 里，见 add-event-detection 的 design D8。
"""

from tradingagentspro.contracts.event import Event, EventCategory, EventSource
from tradingagentspro.contracts.news import Filing
from tradingagentspro.modules.events.config import EventRulesConfig
from tradingagentspro.modules.events.domain.earnings import filing_earnings_event
from tradingagentspro.modules.events.domain.ids import Detection
from tradingagentspro.modules.events.domain.news import (
    Discard,
    DiscardReason,
    Monitored,
    Screened,
    screened_event,
)


def judge_filing(
    filing: Filing, stock: Monitored, at: Detection, config: EventRulesConfig
) -> Event | Discard:
    """一份公告对一只监控股票：从上往下找第一行符合的；8-K 的 item 2.02 是财报发布事件。"""
    if filing.accepted_at < stock.added_at:
        return Discard(DiscardReason.BEFORE_ADDED)
    tiers = config.filing_tiers
    items = set(filing.items)
    if filing.form in tiers.current_reports:
        if items & set(tiers.earnings_items):
            return filing_earnings_event(filing, stock.symbol, at)
        if items & set(tiers.negative_items):
            level, negative = 2, True
        elif items & set(tiers.major_items):
            level, negative = 2, False
        else:
            level, negative = 1, False
    elif filing.form in tiers.offerings:
        level, negative = 2, True
    elif filing.form in tiers.major_forms:
        level, negative = 2, False
    elif filing.form in tiers.ordinary_forms:
        level, negative = 1, False
    else:
        return Discard(DiscardReason.FORM)
    listed = f"，item {'、'.join(filing.items)}" if filing.items else ""
    screened = Screened(
        category=EventCategory.FILING,
        source=EventSource.FILINGS,
        level=level,
        negative=negative,
        occurred_at=filing.accepted_at,
        refs=(filing.accession_number,),
        summary=f"公告 {filing.form}{listed}（{filing.accession_number}）",
    )
    return screened_event(screened, stock, at)
