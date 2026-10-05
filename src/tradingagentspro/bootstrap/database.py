"""数据库的启动步骤：把状态库升级到最新的迁移版本，见 add-state-store 的 design D6。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

MIGRATIONS = Path(__file__).with_name("migrations")


def migrate(engine: Engine) -> None:
    """在一个事务里把数据库升级到最新版本；已经是最新版本时什么都不做。"""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
