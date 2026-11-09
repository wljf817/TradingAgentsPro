"""建出量化指标表 gateway_indicators，见 update-quant-entry 的 design D4。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0019"
down_revision: str | None = "0018"


def upgrade() -> None:
    op.create_table(
        "gateway_indicators",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("data", JSONB(), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
