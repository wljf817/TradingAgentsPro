"""News 的读取工具，见 news-tools spec 和 add-news-tools 的 design D6、D7。"""

import re
from collections.abc import Sequence
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict

from tradingagentspro.contracts.agent_call import SubAgentRequest
from tradingagentspro.contracts.news import NewsArticle
from tradingagentspro.integrations.llm.client import ReadTool
from tradingagentspro.integrations.llm.sub_agent import RecordEntries, ToolBuilder
from tradingagentspro.modules.news import NewsLookupService, NewsSearch

# 新闻 ID 是 PostgreSQL 的 BIGINT，位数再多的不可能是新闻 ID
_ENTRY_ID: Final = re.compile(r"news/(\d{1,18})")
_LINE_TAGS: Final = frozenset(
    "p div br li tr td th h1 h2 h3 h4 h5 h6 blockquote hr ul ol table".split()
)
_HIDDEN_TAGS: Final = frozenset({"script", "style"})

_EMPTY_RANGE: Final = "这个日期范围是空的，没有查。"
_NO_BODY: Final = "这条新闻没有正文。"
_NOT_FOUND: Final = "查不到这条新闻。"


class _Row(BaseModel):
    """list_news 的一条。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    headline: str
    summary: str
    source: str
    published_at: AwareDatetime
    tagged_symbols: int
    has_body: bool


class _Listing(BaseModel):
    """list_news 的结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    articles: tuple[_Row, ...]
    total: int
    notices: tuple[str, ...]


class _Article(BaseModel):
    """read_news 读到的一条。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    headline: str
    summary: str
    source: str
    symbols: tuple[str, ...]
    published_at: AwareDatetime
    body: str
    notices: tuple[str, ...]


class _Missing(BaseModel):
    """read_news 查不到。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notices: tuple[str, ...] = (_NOT_FOUND,)


class _TextParser(HTMLParser):
    """收集 HTML 里的文字：段落一类的标签换行，脚本和样式的内容不要。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden = True
        elif tag in _LINE_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden = False
        elif tag in _LINE_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def news_tools(lookup: NewsLookupService) -> ToolBuilder:
    """给出 News 这次运行的读取工具：股票是任务的股票，时刻是这版快照的生成时刻。"""

    def build(request: SubAgentRequest, record: RecordEntries) -> Sequence[ReadTool]:
        symbol = request.task.symbol
        before = request.snapshot.taken_at

        def list_news(
            since: date | None = None, until: date | None = None, keyword: str | None = None
        ) -> str:
            search = lookup.search(symbol, before, since, until, keyword)
            record(_entry_id(article) for article in search.articles)
            return _listing(search).model_dump_json()

        def read_news(entry_id: str) -> str:
            matched = _ENTRY_ID.fullmatch(entry_id)
            article = lookup.read(symbol, before, int(matched[1])) if matched else None
            if article is None:
                return _Missing().model_dump_json()
            record([entry_id])
            return _article(article).model_dump_json()

        return (
            ReadTool("list_news", "列出这只股票的新闻", list_news),
            ReadTool("read_news", "读一条新闻的全文", read_news),
        )

    return build


def _entry_id(article: NewsArticle) -> str:
    return f"news/{article.id}"


def _plain_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    lines = (line.strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def _time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%SZ")


def _notices(search: NewsSearch) -> tuple[str, ...]:
    if search.empty_range:
        return (_EMPTY_RANGE,)
    notices = []
    if search.total > len(search.articles):
        notices.append(
            f"这里只有最新的 {len(search.articles)} 条，缩小日期或者加关键词可以看到别的。"
        )
    if search.floor is not None:
        notices.append(f"{_time(search.floor)} 以前的新闻查不到。")
    if search.incomplete is not None:
        start, end = search.incomplete
        notices.append(f"新闻源不可用，{_time(start)} 到 {_time(end)} 的新闻可能不全。")
    return tuple(notices)


def _listing(search: NewsSearch) -> _Listing:
    return _Listing(
        articles=tuple(
            _Row(
                entry_id=_entry_id(article),
                headline=article.headline,
                summary=article.summary,
                source=article.source,
                published_at=article.published_at,
                tagged_symbols=len(article.symbols),
                has_body=bool(_plain_text(article.content)),
            )
            for article in search.articles
        ),
        total=search.total,
        notices=_notices(search),
    )


def _article(article: NewsArticle) -> _Article:
    body = _plain_text(article.content)
    return _Article(
        entry_id=_entry_id(article),
        headline=article.headline,
        summary=article.summary,
        source=article.source,
        symbols=article.symbols,
        published_at=article.published_at,
        body=body,
        notices=() if body else (_NO_BODY,),
    )
