"""Fundamentals 的读取工具，见 filing-tools spec 和 add-filing-tools 的 design D9。"""

import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated, Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from tradingagentspro.contracts.agent_call import SubAgentRequest
from tradingagentspro.contracts.news import Filing, FilingDocument, NewsArticle
from tradingagentspro.integrations.llm.client import ReadTool
from tradingagentspro.integrations.llm.news import plain_text
from tradingagentspro.integrations.llm.sub_agent import RecordEntries, ToolBuilder
from tradingagentspro.modules.news import (
    FilingLookupService,
    FilingMatches,
    FilingPage,
    FilingSearch,
    TranscriptSearch,
)

# accession number：10 位、2 位、6 位数字
_ENTRY_ID: Final = re.compile(r"filing/(\d{10}-\d{2}-\d{6})")

_BAD_FORM: Final = "表单类型只能是 8-K、6-K、10-Q、10-K、20-F。"
_EMPTY_RANGE: Final = "这个日期范围是空的，没有查。"
_NOT_FOUND: Final = "查不到这份公告。"
_UNAVAILABLE: Final = "现在取不到这份公告的文件，可以稍后再试。"
_NO_DOCUMENTS: Final = "这份公告没有能读的文件。"
_PAST_END: Final = "起点超过了文件的字数。"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Row(_Frozen):
    """list_filings 的一份。"""

    entry_id: str
    form: str
    items: tuple[str, ...]
    accepted_at: AwareDatetime


class _Listing(_Frozen):
    """list_filings 的结果。"""

    filings: tuple[_Row, ...]
    total: int
    notices: tuple[str, ...]


class _Document(_Frozen):
    """文件列表的一项。"""

    doc_type: str
    name: str
    chars: int


class _Header(_Frozen):
    """read_filing 结果的公告部分。"""

    entry_id: str
    form: str
    items: tuple[str, ...]
    accepted_at: AwareDatetime
    documents: tuple[_Document, ...]


class _Page(_Header):
    """read_filing 读到的一段。"""

    document: str
    start: int
    text: str
    chars: int
    next_start: int | None
    notices: tuple[str, ...]


class _Match(_Frozen):
    position: int
    line: str


class _Found(_Header):
    """read_filing 查找的结果。"""

    document: str
    start: int
    matches: tuple[_Match, ...]
    total: int
    notices: tuple[str, ...]


class _NoDocument(_Header):
    """read_filing 的文件列表里没有这个类型。"""

    notices: tuple[str, ...]


class _Notice(_Frozen):
    """只给出说明。"""

    notices: tuple[str, ...]


class _Transcript(_Frozen):
    """read_transcript 找到的一篇。"""

    entry_id: str
    headline: str
    published_at: AwareDatetime
    body: str
    notices: tuple[str, ...]


def filing_tools(lookup: FilingLookupService) -> ToolBuilder:
    """给出 Fundamentals 这次运行的读取工具：股票是任务的股票，时刻是这版快照的生成时刻。"""

    def build(request: SubAgentRequest, record: RecordEntries) -> Sequence[ReadTool]:
        symbol = request.task.symbol
        before = request.snapshot.taken_at

        def list_filings(
            form: str | None = None, since: date | None = None, until: date | None = None
        ) -> str:
            search = lookup.search(symbol, before, form, since, until)
            record(_entry_id(filing) for filing in search.filings)
            return _listing(search).model_dump_json()

        def read_filing(
            entry_id: str,
            document: str | None = None,
            start: Annotated[int, Field(ge=0)] = 0,
            find: str | None = None,
        ) -> str:
            matched = _ENTRY_ID.fullmatch(entry_id)
            filing = lookup.filing(symbol, before, matched[1]) if matched else None
            if filing is None:
                return _Notice(notices=(_NOT_FOUND,)).model_dump_json()
            documents = lookup.documents(filing)
            if documents is None:
                return _Notice(notices=(_UNAVAILABLE,)).model_dump_json()
            if not documents:
                return _Notice(notices=(_NO_DOCUMENTS,)).model_dump_json()
            header = _header(filing, documents)
            chosen = _choose(documents, document)
            if chosen is None:
                notice = f"没有 {document} 这个文件。"
                no_document = _NoDocument.model_validate(header | {"notices": (notice,)})
                return no_document.model_dump_json()
            record([entry_id])
            if find is not None:
                found = lookup.find(chosen.text, find, start)
                return _found(header, chosen, start, found).model_dump_json()
            return _page(header, chosen, start, lookup.page(chosen.text, start)).model_dump_json()

        def read_transcript(until: date | None = None) -> str:
            search = lookup.transcript(symbol, before, until)
            if search.article is None:
                return _Notice(notices=_transcript_notices(search)).model_dump_json()
            record([_news_id(search.article)])
            return _transcript(search.article, search).model_dump_json()

        return (
            ReadTool("list_filings", "列出这只股票能读的公告", list_filings),
            ReadTool(
                "read_filing", "读一份公告里的一个文件，或者在文件里查找一段文字", read_filing
            ),
            ReadTool("read_transcript", "读一篇财报电话会文字稿", read_transcript),
        )

    return build


def _entry_id(filing: Filing) -> str:
    return f"filing/{filing.accession_number}"


def _news_id(article: NewsArticle) -> str:
    return f"news/{article.id}"


def _time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%SZ")


def _choose(documents: tuple[FilingDocument, ...], document: str | None) -> FilingDocument | None:
    """不写文件时是主文件；写了就按类型找，不分大小写。"""
    if document is None:
        return documents[0]
    wanted = document.strip().upper()
    return next((d for d in documents if d.doc_type.upper() == wanted), None)


def _listing(search: FilingSearch) -> _Listing:
    return _Listing(
        filings=tuple(
            _Row(
                entry_id=_entry_id(filing),
                form=filing.form,
                items=filing.items,
                accepted_at=filing.accepted_at,
            )
            for filing in search.filings
        ),
        total=search.total,
        notices=_list_notices(search),
    )


def _list_notices(search: FilingSearch) -> tuple[str, ...]:
    if search.bad_form:
        return (_BAD_FORM,)
    if search.empty_range:
        return (_EMPTY_RANGE,)
    notices = []
    if search.total > len(search.filings):
        notices.append(
            f"这里只有最新的 {len(search.filings)} 份，缩小日期或者按表单类型筛可以看到别的。"
        )
    if search.floor is not None:
        notices.append(f"{_time(search.floor)} 以前的公告查不到。")
    if search.incomplete is not None:
        start, end = search.incomplete
        notices.append(f"公告源不可用，{_time(start)} 到 {_time(end)} 的公告可能不全。")
    return tuple(notices)


def _header(filing: Filing, documents: tuple[FilingDocument, ...]) -> dict[str, object]:
    return {
        "entry_id": _entry_id(filing),
        "form": filing.form,
        "items": filing.items,
        "accepted_at": filing.accepted_at,
        "documents": tuple(
            _Document(doc_type=d.doc_type, name=d.name, chars=len(d.text)) for d in documents
        ),
    }


def _page(header: dict[str, object], chosen: FilingDocument, start: int, page: FilingPage) -> _Page:
    notices = []
    if page.past_end:
        notices.append(_PAST_END)
    if page.next_start is not None:
        notices.append(f"后面还有，下一次从 {page.next_start} 读起。")
    return _Page.model_validate(
        header
        | {
            "document": chosen.doc_type,
            "start": start,
            "text": page.text,
            "chars": page.length,
            "next_start": page.next_start,
            "notices": tuple(notices),
        }
    )


def _found(
    header: dict[str, object], chosen: FilingDocument, start: int, found: FilingMatches
) -> _Found:
    notices: tuple[str, ...] = ()
    if found.total > len(found.matches):
        resume = found.matches[-1].position + 1
        notices = (f"还有更多，可以从 {resume} 起接着查。",)
    return _Found.model_validate(
        header
        | {
            "document": chosen.doc_type,
            "start": start,
            "matches": tuple(_Match(position=m.position, line=m.line) for m in found.matches),
            "total": found.total,
            "notices": notices,
        }
    )


def _transcript_notices(search: TranscriptSearch) -> tuple[str, ...]:
    if search.empty_range:
        return (_EMPTY_RANGE,)
    notices = [f"{_time(search.start)} 到 {_time(search.end)} 没有找到文字稿。"]
    return (*notices, *_window_notices(search))


def _window_notices(search: TranscriptSearch) -> tuple[str, ...]:
    notices = []
    if search.floor is not None:
        notices.append(f"{_time(search.floor)} 以前查不到。")
    if search.incomplete is not None:
        start, end = search.incomplete
        notices.append(f"新闻源不可用，{_time(start)} 到 {_time(end)} 的新闻可能不全。")
    return tuple(notices)


def _transcript(article: NewsArticle, search: TranscriptSearch) -> _Transcript:
    return _Transcript(
        entry_id=_news_id(article),
        headline=article.headline,
        published_at=article.published_at,
        body=plain_text(article.content),
        notices=_window_notices(search),
    )
