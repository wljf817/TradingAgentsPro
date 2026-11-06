"""给交易记录表 execution_trades 加股票列，已有的版本从记录内容回填，见 add-query-history 的
design D5。
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"


def upgrade() -> None:
    op.add_column("execution_trades", sa.Column("symbol", sa.String(), nullable=True))
    op.execute("UPDATE execution_trades SET symbol = data->>'symbol'")
    op.alter_column("execution_trades", "symbol", nullable=False)


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
