"""建出调度器的两张表：排队表 scheduler_queue 和出队表 scheduler_queue_exits。

两张表都只追加、一行一条：排队表的主键是案件 ID，position 是入队的先后；出队表的主键是案件 ID，
一个案件只出队一次。见 add-queue-runner 的 design D2。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: str | None = "0012"


def upgrade() -> None:
    op.create_table(
        "scheduler_queue",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("trigger", JSONB(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_table(
        "scheduler_queue_exits",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("case_id"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
