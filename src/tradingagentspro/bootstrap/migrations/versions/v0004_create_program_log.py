"""建出程序日志的表 programlog_entries 和它按时间的索引。

只追加：一个动作一行，编号自增；可以为空的列是案件 ID、耗时、token 和费用，
见 add-program-log 的 design D1。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0004"
down_revision: str | None = "0003"


def upgrade() -> None:
    op.create_table(
        "programlog_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
        sa.Column("inputs", ARRAY(sa.String()), nullable=False),
        sa.Column("outputs", ARRAY(sa.String()), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("duration", sa.Interval(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(), nullable=True),
    )
    op.create_index("programlog_entries_recorded_at", "programlog_entries", ["recorded_at"])


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
