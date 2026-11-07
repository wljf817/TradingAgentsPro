"""建出公告文件表 news_filing_documents，见 add-filing-tools 的 design D6。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"


def upgrade() -> None:
    op.create_table(
        "news_filing_documents",
        sa.Column("accession_number", sa.String(), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
