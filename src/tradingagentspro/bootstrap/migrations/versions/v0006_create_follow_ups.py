"""建出跟进模块的跟进表 followups_follow_ups。

和别的版本表一样只追加：键是跟进 ID；筛选列 pending 列出待执行的跟进，requested_at 是获批时刻，
列出当天获批的跟进。见 update-follow-up-storage 的 design D1、D7。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006"
down_revision: str | None = "0005"


def upgrade() -> None:
    op.create_table(
        "followups_follow_ups",
        sa.Column("follow_up_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("follow_up_id", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
