"""新闻和公告的条目：新闻和公告模块存下它们，事件检测、状态库快照和 Agent 网关读它们。

见 news-and-filings spec「新闻和公告的条目」和 add-news-and-filings 的 design D2。
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict


class NewsArticle(BaseModel):
    """一条新闻。数据来自新闻源，原样保存，不加约束。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int  # 新闻源给的编号，用来去重
    headline: str
    summary: str
    content: str  # 正文，HTML；新闻源没有正文时为空
    author: str
    source: str  # 稿件来源，比如 benzinga
    url: str | None  # 原文地址
    symbols: tuple[str, ...]  # 新闻源标注的全部股票，按新闻源给的顺序
    published_at: AwareDatetime


class Filing(BaseModel):
    """一份 SEC 公告。数据来自公告源，原样保存，不加约束。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accession_number: str  # SEC 给的编号，比如 0000320193-26-000018，用来去重
    symbols: tuple[str, ...]  # 问到的股票里属于这份公告所列公司的
    form: str  # 表单类型，SEC 的写法，比如 8-K、10-Q、SCHEDULE 13D/A、4
    items: tuple[str, ...]  # 8-K 和 8-K/A 列出的 item，比如 2.02；别的表单为空
    accepted_at: AwareDatetime  # SEC 受理的时刻
    url: str  # 主文件地址
