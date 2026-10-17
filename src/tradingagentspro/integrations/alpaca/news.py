"""Alpaca 新闻源：用 Alpaca 市场数据 API 的新闻接口（Benzinga 稿件）实现新闻和公告模块的
NewsSource。

见 alpaca-news spec 和 add-news-and-filings 的 design D5。
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from tradingagentspro.contracts.news import NewsArticle
from tradingagentspro.modules.news import SourceUnavailableError

DATA_URL = "https://data.alpaca.markets"

# 每页最多多少条：Alpaca 允许的上限
_PAGE_SIZE = 50


class AlpacaNewsConfig(BaseModel):
    """Alpaca 新闻源的配置，见 alpaca-news spec「Alpaca 新闻配置」。key 和模拟盘用同一组。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    secret_key: SecretStr
    base_url: str = DATA_URL
    # 每次请求等多久没有响应就算超时
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class _Article(BaseModel):
    """Alpaca 的一条新闻：只取用到的字段。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int
    headline: str
    summary: str
    content: str
    author: str
    source: str
    url: str | None
    symbols: tuple[str, ...]
    created_at: AwareDatetime


class _Page(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    news: tuple[_Article, ...]
    next_page_token: str | None


class AlpacaNewsSource:
    """满足 NewsSource：一次查询取一组股票的新闻，取完全部分页。

    调用失败按 alpaca-news spec「新闻请求失败的分类」处理：超时、连不上、429 和 5xx 抛
    SourceUnavailableError；其余的 4xx 原样抛 httpx.HTTPStatusError；回复读不出来抛 pydantic 的
    ValidationError。任何一页失败整次失败，不重试。
    """

    def __init__(
        self, config: AlpacaNewsConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        """transport 只给测试用：传入 httpx.MockTransport 代替真实的 Alpaca。"""
        self._http = httpx.Client(
            base_url=config.base_url,
            headers={
                "APCA-API-KEY-ID": config.key_id,
                "APCA-API-SECRET-KEY": config.secret_key.get_secret_value(),
            },
            timeout=config.timeout.total_seconds(),
            transport=transport,
        )

    def articles(self, symbols: Sequence[str], since: datetime) -> tuple[NewsArticle, ...]:
        """since（含）以来发布的新闻。start 舍去秒以下的部分，回来以后去掉发布时刻早于 since 的。"""
        params: dict[str, str] = {
            "symbols": ",".join(symbols),
            "start": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "include_content": "true",
            "limit": str(_PAGE_SIZE),
            "sort": "asc",
        }
        found: list[NewsArticle] = []
        token: str | None = None
        while True:
            page = _Page.model_validate_json(self._get(params, token).content)
            found.extend(_article(a) for a in page.news if a.created_at >= since)
            token = page.next_page_token
            if token is None:
                return tuple(found)

    def _get(self, params: dict[str, str], token: str | None) -> httpx.Response:
        """请求一页。超时、连不上、429 和 5xx 抛 SourceUnavailableError，其余的 4xx 原样抛出。"""
        query = params if token is None else {**params, "page_token": token}
        path = "/v1beta1/news"
        try:
            response = self._http.get(path, params=query)
        except httpx.TransportError as error:  # 超时也是 TransportError
            raise SourceUnavailableError(f"Alpaca 新闻 GET {path}：{error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise SourceUnavailableError(f"Alpaca 新闻 GET {path}：{response.status_code}")
        return response.raise_for_status()


def _article(article: _Article) -> NewsArticle:
    return NewsArticle(
        id=article.id,
        headline=article.headline,
        summary=article.summary,
        content=article.content,
        author=article.author,
        source=article.source,
        url=article.url,
        symbols=article.symbols,
        published_at=article.created_at.astimezone(UTC),
    )
