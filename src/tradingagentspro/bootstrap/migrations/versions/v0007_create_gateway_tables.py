"""建出 Agent 网关的三张表：任务表 gateway_tasks、Result 表 gateway_results、费用表 gateway_costs，
以及按案件取任务、按记下的时刻取费用的两个索引。

三张表都只追加、一行一条：Task 和 Result 存整份 JSON，一个 Task 最多一行 Result（主键是
task_id）；费用记录拆成列。见 update-task-cost-storage 的 design D1。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: str | None = "0006"


def upgrade() -> None:
    op.create_table(
        "gateway_tasks",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
    )
    op.create_index("gateway_tasks_case_id", "gateway_tasks", ["case_id"])
    op.create_table(
        "gateway_results",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("data", JSONB(), nullable=False),
    )
    op.create_table(
        "gateway_costs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_type", sa.String(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("model_vendor", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Numeric(), nullable=False),
        sa.Column("budget", sa.String(), nullable=False),
    )
    op.create_index("gateway_costs_recorded_at", "gateway_costs", ["recorded_at"])


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
