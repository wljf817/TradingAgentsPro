"""新闻表 news_articles、公告表 news_filings、公告文件表 news_filing_documents。

一条新闻、一份公告、公告的一个文件各一行；只追加，没有修改和删除。见 news-and-filings spec
「新闻和公告的存储」、add-news-and-filings 的 design D4 和 add-filing-tools 的 design D6。
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
    Integer,
    MetaData,
    Select,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tradingagentspro.contracts.news import Filing, FilingDocument, NewsArticle
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

DOCUMENTS = Table(
    "news_filing_documents",
    MetaData(),
    Column("accession_number", String, primary_key=True),
    Column("position", Integer, primary_key=True, autoincrement=False),  # 在公告里的顺序，从 0 算
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("doc_type", String, nullable=False),
    Column("name", String, nullable=False),
    Column("text", Text, nullable=False),
)


class PostgresNewsStore:
    """满足 NewsStore：新闻和公告存在状态库所在数据库的 news_articles、news_filings 表里，条目整份
    存成 JSON；公告的文件存在 news_filing_documents 表里，一个文件一行。每个操作一个事务；数据库的
    错误、读不出来的条目原样抛出。
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

    def article(self, article_id: int) -> NewsArticle | None:
        query = select(ARTICLES.c.data).where(ARTICLES.c.id == article_id)
        rows = self._read(query)
        return NewsArticle.model_validate(rows[0]) if rows else None

    def filings(self, symbol: str, since: datetime) -> tuple[Filing, ...]:
        query = (
            select(FILINGS.c.data)
            .where(FILINGS.c.symbols.contains([symbol]), FILINGS.c.accepted_at >= since)
            .order_by(FILINGS.c.accepted_at, FILINGS.c.accession_number)
        )
        return tuple(Filing.model_validate(data) for data in self._read(query))

    def filing(self, accession_number: str) -> Filing | None:
        query = select(FILINGS.c.data).where(FILINGS.c.accession_number == accession_number)
        rows = self._read(query)
        return Filing.model_validate(rows[0]) if rows else None

    def add_documents(self, accession_number: str, documents: Sequence[FilingDocument]) -> None:
        """一条语句写入全部文件；同时写入同一份公告时，后到的等先到的结束，然后什么都不做。"""
        recorded_at = self._clock.now()
        rows = [
            {
                "accession_number": accession_number,
                "position": position,
                "recorded_at": recorded_at,
                "doc_type": document.doc_type,
                "name": document.name,
                "text": document.text,
            }
            for position, document in enumerate(documents)
        ]
        write = pg_insert(DOCUMENTS).values(rows).on_conflict_do_nothing()
        with self._engine.begin() as connection:
            connection.execute(write)

    def documents(self, accession_number: str) -> tuple[FilingDocument, ...]:
        query = (
            select(DOCUMENTS.c.doc_type, DOCUMENTS.c.name, DOCUMENTS.c.text)
            .where(DOCUMENTS.c.accession_number == accession_number)
            .order_by(DOCUMENTS.c.position)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
        return tuple(FilingDocument(doc_type=t, name=n, text=x) for t, n, x in rows)

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
