"""建出财务事实表 companydata_financial_facts，见 add-financial-figures 的 design D2。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"


def upgrade() -> None:
    op.create_table(
        "companydata_financial_facts",
        sa.Column("symbol", sa.String(), primary_key=True),
        sa.Column("concept", sa.String(), primary_key=True),
        sa.Column("unit", sa.String(), primary_key=True),
        sa.Column("period_start", sa.Date(), primary_key=True),
        sa.Column("period_end", sa.Date(), primary_key=True),
        sa.Column("accession_number", sa.String(), primary_key=True),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("form", sa.String(), nullable=False),
        sa.Column("filed", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
