"""Alembic 的运行环境：只用调用方传进来的连接，见 bootstrap.database.migrate。

不读环境变量，也不 import 业务代码：迁移脚本写明每一列，见 add-state-store 的 design D6。
"""

from alembic import context

context.configure(connection=context.config.attributes["connection"])
with context.begin_transaction():
    context.run_migrations()
