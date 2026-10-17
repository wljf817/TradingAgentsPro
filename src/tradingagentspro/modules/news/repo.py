"""新闻表 news_articles、公告表 news_filings。

一条新闻、一份公告各一行；只追加，没有修改和删除。见 news-and-filings spec「新闻和公告的存储」
和 add-news-and-filings 的 design D4。
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Engine,
    Index,
    MetaData,
    Select,
    String,
    Table,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tradingagentspro.contracts.news import Filing, NewsArticle
from tradingagentspro.shared.clock import Clock

ARTICLES = Table(
    "news_articles",
    MetaData(),
    Column("id", BigInteger, primary_key=True, autoincrement=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("symbols", ARRAY(String), nullable=False),
    Column("data", JSONB, nullable=False),
    Index("news_articles_published_at", "published_at"),
)

FILINGS = Table(
    "news_filings",
    MetaData(),
    Column("accession_number", String, primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=False),
    Column("symbols", ARRAY(String), nullable=False),
    Column("data", JSONB, nullable=False),
    Index("news_filings_accepted_at", "accepted_at"),
)


class PostgresNewsStore:
    """满足 NewsStore：新闻和公告存在状态库所在数据库的 news_articles、news_filings 表里，条目整份
    存成 JSON。每个操作一个事务；数据库的错误、读不出来的条目原样抛出。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def add_articles(self, articles: Sequence[NewsArticle]) -> tuple[NewsArticle, ...]:
        written = self._insert(
            ARTICLES,
            [
                {
                    "id": a.id,
                    "published_at": a.published_at,
                    "symbols": list(a.symbols),
                    "data": a.model_dump(mode="json"),
                }
                for a in articles
            ],
        )
        return tuple(a for a in articles if a.id in written)

    def add_filings(self, filings: Sequence[Filing]) -> tuple[Filing, ...]:
        written = self._insert(
            FILINGS,
            [
                {
                    "accession_number": f.accession_number,
                    "accepted_at": f.accepted_at,
                    "symbols": list(f.symbols),
                    "data": f.model_dump(mode="json"),
                }
                for f in filings
            ],
        )
        return tuple(f for f in filings if f.accession_number in written)

    def articles(self, symbol: str, since: datetime) -> tuple[NewsArticle, ...]:
        query = (
            select(ARTICLES.c.data)
            .where(ARTICLES.c.symbols.contains([symbol]), ARTICLES.c.published_at >= since)
            .order_by(ARTICLES.c.published_at, ARTICLES.c.id)
        )
        return tuple(NewsArticle.model_validate(data) for data in self._read(query))

    def filings(self, symbol: str, since: datetime) -> tuple[Filing, ...]:
        query = (
            select(FILINGS.c.data)
            .where(FILINGS.c.symbols.contains([symbol]), FILINGS.c.accepted_at >= since)
            .order_by(FILINGS.c.accepted_at, FILINGS.c.accession_number)
        )
        return tuple(Filing.model_validate(data) for data in self._read(query))

    def _insert(self, table: Table, rows: Sequence[Mapping[str, object]]) -> set[object]:
        """一条语句写入还没有的行，给出真正写入的键。见 design D4：另一个事务正在写同一个键时等它
        结束，然后什么都不做。没有行时不访问数据库（空的 VALUES 不是合法的 SQL）。
        """
        if not rows:
            return set()
        key = next(iter(table.primary_key.columns))
        recorded_at = self._clock.now()
        write = (
            pg_insert(table)
            .values([{**row, "recorded_at": recorded_at} for row in rows])
            .on_conflict_do_nothing(index_elements=[key])
            .returning(key)
        )
        with self._engine.begin() as connection:
            return set(connection.execute(write).scalars())

    def _read(self, query: Select[Any]) -> Sequence[Any]:
        with self._engine.connect() as connection:
            return connection.execute(query).scalars().all()
