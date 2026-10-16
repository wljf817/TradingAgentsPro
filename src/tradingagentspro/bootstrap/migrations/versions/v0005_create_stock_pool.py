"""建出股票池的记录表 stockpool_entries。

和别的版本表一样只追加：键是股票代码；筛选列 in_pool 列出池子里的股票，added_at、removed_at 列出
当天有增删的记录，removed_at 可以为空。见 update-stock-pool-storage 的 design D3。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"


def upgrade() -> None:
    op.create_table(
        "stockpool_entries",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("in_pool", sa.Boolean(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("symbol", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
