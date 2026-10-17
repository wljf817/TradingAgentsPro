"""建出寻股粗筛的粗筛结果表 screener_runs。

和别的版本表一样只追加：键是截止交易日，没有筛选列。见 add-stock-screen 的 design D9。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: str | None = "0009"


def upgrade() -> None:
    op.create_table(
        "screener_runs",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("day", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
