"""公告工具的规则：能读的表单、列表的范围和筛选、按位置读、查找，见 filing-tools spec「列出公告」
「读公告」和 add-filing-tools 的 design D2、D3、D7。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from tradingagentspro.contracts.news import Filing
from tradingagentspro.modules.news.config import FilingConfig

READABLE_FORMS: Final = ("8-K", "6-K", "10-Q", "10-K", "20-F")
# 能读的表单和它们的修正版：补抓时只存这些
READABLE_WITH_AMENDMENTS: Final = frozenset(
    form for base in READABLE_FORMS for form in (base, f"{base}/A")
)
_AMENDMENT: Final = "/A"
_LINE_CHARS: Final = 300  # 查找结果里所在的那一行最多给多少字
_APOSTROPHE: Final = "’"


@dataclass(frozen=True, slots=True)
class FilingWindow:
    """要列的范围，两端都含。"""

    start: datetime
    end: datetime
    floor: datetime | None  # 起始被裁到的时刻；没有裁时为 None

    @property
    def empty(self) -> bool:
        return self.start > self.end


@dataclass(frozen=True, slots=True)
class FilingSearch:
    """一次列表的结果。"""

    filings: tuple[Filing, ...]  # 从晚到早
    total: int  # 范围内符合条件的一共多少份
    floor: datetime | None  # 这个时刻以前的查不到；起始没有被裁时为 None
    empty_range: bool
    bad_form: bool  # 表单类型不是能读的五种之一
    incomplete: tuple[datetime, datetime] | None  # 补抓失败的时段


@dataclass(frozen=True, slots=True)
class FilingPage:
    """从起点起读到的一段。"""

    text: str
    length: int  # 文件一共多少字
    next_start: int | None  # 后面还有时下一次的起点
    past_end: bool  # 起点不小于字数


@dataclass(frozen=True, slots=True)
class FilingMatch:
    position: int  # 在纯文本里的字符位置
    line: str  # 所在的那一行，最多 300 字


@dataclass(frozen=True, slots=True)
class FilingMatches:
    """从起点往后查找的结果。"""

    matches: tuple[FilingMatch, ...]
    total: int  # 从起点往后一共多少处


def readable(form: str) -> bool:
    """能读的表单：五种之一，或者它们的修正版。"""
    return form in READABLE_WITH_AMENDMENTS


def listed_form(written: str) -> str | None:
    """模型写的表单类型换成能读的五种之一，不分大小写；不是这五种时为 None。"""
    form = written.strip().upper()
    return form if form in READABLE_FORMS else None


def filing_window(
    before: datetime, start: datetime | None, end: datetime | None, config: FilingConfig
) -> FilingWindow:
    """before 是快照时刻；start、end 是模型写的范围，没写时为 None。"""
    last = before if end is None else min(end, before)
    earliest = before - timedelta(days=config.filing_tool_days)
    if start is None:
        return FilingWindow(earliest, last, None)
    if start < earliest:
        return FilingWindow(earliest, last, earliest)
    return FilingWindow(start, last, None)


def newest_filings(
    stored: Iterable[Filing], window: FilingWindow, form: str | None, limit: int
) -> tuple[tuple[Filing, ...], int]:
    """范围内能读、符合表单类型的最新 limit 份，从晚到早，和一共多少份。修正版按原表单算。"""
    found = [
        filing
        for filing in stored
        if window.start <= filing.accepted_at <= window.end
        and readable(filing.form)
        and (form is None or filing.form.removesuffix(_AMENDMENT) == form)
    ]
    found.sort(key=lambda f: (f.accepted_at, f.accession_number), reverse=True)
    return tuple(found[:limit]), len(found)


def filing_page(text: str, start: int, limit: int) -> FilingPage:
    """从 start 起最多 limit 字。"""
    end = start + limit
    return FilingPage(
        text=text[start:end],
        length=len(text),
        next_start=end if end < len(text) else None,
        past_end=start >= len(text),
    )


def find_in_text(text: str, phrase: str, start: int, limit: int) -> FilingMatches:
    """从 start 往后查找 phrase：不分大小写，’ 当作 '；位置按 text 算，见 design D7。"""
    pattern = re.compile(re.escape(phrase.replace(_APOSTROPHE, "'")), re.IGNORECASE)
    positions = [m.start() for m in pattern.finditer(text.replace(_APOSTROPHE, "'"), start)]
    return FilingMatches(
        matches=tuple(FilingMatch(p, _line(text, p)) for p in positions[:limit]),
        total=len(positions),
    )


def _line(text: str, position: int) -> str:
    """position 所在的那一行，超过 300 字的只给前 300 字。"""
    first = text.rfind("\n", 0, position) + 1
    last = text.find("\n", position)
    return text[first : len(text) if last == -1 else last][:_LINE_CHARS]
