"""建出执行模块的收盘对账记录表 execution_close_reconciliations。

和别的表一样只追加：键是交易日，没有筛选列，见 add-close-reconciliation 的 design D6。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | None = "0002"


def upgrade() -> None:
    op.create_table(
        "execution_close_reconciliations",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("day", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
