"""建出公司数据模块的两张版本表：财报日历表 companydata_earnings_releases、surprise 表
companydata_earnings_surprises。

和别的版本表一样只追加：财报日历的键是一次发布的键（股票代码和财年季度），筛选列 symbol 用来按股票
列出；surprise 的键是股票代码。见 add-earnings-calendar-and-sector 的 design D5。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: str | None = "0008"


def upgrade() -> None:
    op.create_table(
        "companydata_earnings_releases",
        sa.Column("release_key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("release_key", "version"),
    )
    op.create_table(
        "companydata_earnings_surprises",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
