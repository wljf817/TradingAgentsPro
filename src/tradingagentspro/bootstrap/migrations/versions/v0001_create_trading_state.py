"""建出交易侧的三张状态表：风控的预留，执行模块的订单和止损保护。

每张表只追加：键、版本号、写入时刻、记录内容和筛选列，主键是键和版本号，
见 add-state-store 的 design D1。
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None


def _versioned(name: str, key: sa.Column[Any], *columns: sa.Column[Any]) -> None:
    op.create_table(
        name,
        key,
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        *columns,
        sa.PrimaryKeyConstraint(key.name, "version"),
    )


def upgrade() -> None:
    _versioned(
        "risk_reservations",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    _versioned(
        "execution_orders",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    _versioned(
        "execution_protections",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
