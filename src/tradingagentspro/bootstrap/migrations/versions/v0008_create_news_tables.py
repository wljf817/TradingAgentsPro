"""建出新闻和公告模块的两张表：新闻表 news_articles、公告表 news_filings，以及按发布时刻、
受理时刻的两个索引。

两张表都只追加、一条一行：主键是新闻 ID 和 accession number，条目整份存成 JSON，标注的股票和
时刻另存一列用来筛选。见 add-news-and-filings 的 design D4。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0008"
down_revision: str | None = "0007"


def upgrade() -> None:
    op.create_table(
        "news_articles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbols", ARRAY(sa.String()), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
    )
    op.create_index("news_articles_published_at", "news_articles", ["published_at"])
    op.create_table(
        "news_filings",
        sa.Column("accession_number", sa.String(), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbols", ARRAY(sa.String()), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
    )
    op.create_index("news_filings_accepted_at", "news_filings", ["accepted_at"])


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
