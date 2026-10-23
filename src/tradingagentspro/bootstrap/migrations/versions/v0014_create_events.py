"""建出事件模块的事件表 events_detected，以及按股票和检测时刻的索引。

只追加、一个事件一行：主键是事件 ID，同一个事件 ID 只能写一次；事件整份存成 JSON，股票和检测时刻
另存一列用来筛选。见 add-event-detection 的 design D16。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014"
down_revision: str | None = "0013"


def upgrade() -> None:
    op.create_table(
        "events_detected",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", JSONB(), nullable=False),
    )
    op.create_index("events_detected_symbol", "events_detected", ["symbol", "detected_at"])


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
