"""建出执行模块的交易记录表 execution_trades。

和 v0001 的三张表一样只追加：键是客户端订单 ID，筛选列 traded 表示累计成交大于 0，
见 add-trade-records 的 design D1。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "execution_trades",
        sa.Column("client_order_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("traded", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("client_order_id", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
