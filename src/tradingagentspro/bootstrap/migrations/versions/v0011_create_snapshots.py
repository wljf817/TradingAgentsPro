"""建出状态库模块的快照表 statestore_snapshots。

按案件 ID 和版本只追加：主键是案件 ID 和版本，版本号是快照自己的版本。见 add-state-snapshot 的
design D6。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: str | None = "0010"


def upgrade() -> None:
    op.create_table(
        "statestore_snapshots",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("case_id", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
