"""建出案件记录的三张表：案件表 caserecords_cases、方案表 caserecords_proposals、发现表
caserecords_findings。

案件表和方案表是版本表，键是案件 ID；案件表的筛选列 symbol、case_type、status、concluded 按值筛，
concluded_at、ended_at 排序。发现表一项一行、只追加，主键是案件 ID 和第几项。见 add-case-records 的
design D1。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012"
down_revision: str | None = "0011"


def upgrade() -> None:
    op.create_table(
        "caserecords_cases",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("case_type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("concluded", sa.Boolean(), nullable=False),
        sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("case_id", "version"),
    )
    op.create_table(
        "caserecords_proposals",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("case_id", "version"),
    )
    op.create_table(
        "caserecords_findings",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("case_id", "seq"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
