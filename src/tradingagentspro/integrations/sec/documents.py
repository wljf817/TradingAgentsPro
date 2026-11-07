"""SEC 公告的目录页和文件：从目录页的文件表读出文件，把网页转成纯文本。

见 sec-filings spec「公告的文件」和 add-filing-tools 的 design D6。
"""

import re
from html.parser import HTMLParser
from typing import Final

# 要读的附件类型的开头，和能读的文件名结尾
_EXHIBITS: Final = ("EX-99", "EX-13")
_READABLE: Final = (".htm", ".html", ".txt")
# 内容不要的标签；各起一行的标签；前后各加一个空格的标签
_SKIPPED: Final = frozenset({"head", "ix:header", "script", "style"})
_BLOCKS: Final = frozenset(
    {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
)
_CELLS: Final = frozenset({"td", "th"})
_SPACES: Final = re.compile(r"[ \t]+")


class _FileTable(HTMLParser):
    """目录页文件表的每一行：单元格的文字和单元格里链接的地址。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, str | None]]] = []
        self._row: list[tuple[str, str | None]] | None = None
        self._cell: list[str] | None = None
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in _CELLS and self._row is not None:
            self._cell, self._href = [], None
        elif tag == "a" and self._cell is not None:
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in _CELLS and self._row is not None and self._cell is not None:
            self._row.append(("".join(self._cell).strip(), self._href))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def files_to_read(index_page: str) -> list[tuple[str, str]]:
    """目录页里要读的文件的类型和文件名，按表里的顺序：主文件（表里的第一个文件），以及类型以
    EX-99 或 EX-13 开头的附件；文件名不是网页或纯文本的跳过。
    """
    table = _FileTable()
    table.feed(index_page)
    files = [
        (row[3][0], href.rsplit("/", 1)[-1])
        for row in table.rows
        if len(row) >= 4 and (href := row[2][1]) is not None
    ]
    return [
        (doc_type, name)
        for position, (doc_type, name) in enumerate(files)
        if (position == 0 or doc_type.startswith(_EXHIBITS)) and name.lower().endswith(_READABLE)
    ]


class _Text(HTMLParser):
    """网页的文字：跳过不要的标签里的内容，块级标签起一行，单元格前后加空格。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED:
            self._skipping += 1
        self._separate(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skipping -= 1
        self._separate(tag)

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)

    def _separate(self, tag: str) -> None:
        if tag in _BLOCKS:
            self.parts.append("\n")
        elif tag in _CELLS:
            self.parts.append(" ")


def plain_text(name: str, content: str) -> str:
    """文件转成纯文本：网页按 spec 去掉标签和不要的内容；`.txt` 只整理行。"""
    if name.lower().endswith(".txt"):
        lines = (line.strip() for line in content.splitlines())
    else:
        parser = _Text()
        parser.feed(content)
        parser.close()
        text = "".join(parser.parts).replace("\xa0", " ")
        lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)
