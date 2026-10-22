"""环境变量：程序要的全部环境变量在这里一次读完。

见 bootstrap spec「环境变量」和 add-bootstrap-wiring 的 design D3。

VARIABLES 是全部环境变量的唯一清单，「部署」的环境变量模板照它写。错误信息里只有变量名，没有值。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

from tradingagentspro.integrations.alpaca import AlpacaConfig
from tradingagentspro.integrations.discord import DiscordConfig
from tradingagentspro.integrations.finnhub import FinnhubConfig
from tradingagentspro.integrations.sec import SecConfig

DATABASE_URL = "TRADINGAGENTSPRO_DATABASE_URL"
VARIABLES = (
    DATABASE_URL,
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "DISCORD_BOT_TOKEN",
    "DISCORD_GUILD_ID",
    "DISCORD_CHANNEL_ID",
    "DISCORD_USER_ID",
    "FINNHUB_API_KEY",
    "SEC_USER_AGENT",
)

# 状态库只用 PostgreSQL 和 psycopg 3；写 postgresql:// 时 SQLAlchemy 默认找 psycopg2
_DRIVER = "postgresql+psycopg"


class SettingsError(ValueError):
    """环境变量缺少或格式不对。信息里只有变量名，没有值。"""


@dataclass(frozen=True, slots=True)
class Settings:
    """从环境变量读到的配置。repr 里没有密钥：密钥都是 SecretStr。"""

    database_url: SecretStr  # 驱动已经换成 postgresql+psycopg
    alpaca: AlpacaConfig
    discord: DiscordConfig
    finnhub: FinnhubConfig
    sec: SecConfig

    @property
    def database_label(self) -> str:
        """写日志用的数据库地址：密码换成 ***。"""
        return make_url(self.database_url.get_secret_value()).render_as_string(hide_password=True)


def load_settings(environ: Mapping[str, str]) -> Settings:
    """先检查 VARIABLES 有没有缺少（没有或为空），一次全部列出；再逐个解析。"""
    missing = [name for name in VARIABLES if not environ.get(name)]
    if missing:
        raise SettingsError(f"缺少环境变量：{'、'.join(missing)}")
    try:
        discord = DiscordConfig.from_env(environ)
    except ValueError as error:  # 信息里只有变量名
        raise SettingsError(str(error)) from None
    alpaca = AlpacaConfig(
        key_id=environ["APCA_API_KEY_ID"],
        secret_key=SecretStr(environ["APCA_API_SECRET_KEY"]),
    )
    return Settings(
        database_url=_database_url(environ[DATABASE_URL]),
        alpaca=alpaca,
        discord=discord,
        finnhub=FinnhubConfig(api_key=SecretStr(environ["FINNHUB_API_KEY"])),
        sec=SecConfig(user_agent=environ["SEC_USER_AGENT"]),
    )


def _database_url(text: str) -> SecretStr:
    """解析数据库地址，驱动换成 psycopg。解析失败时只报变量名，不带原来的异常。"""
    try:
        url = make_url(text)
    except (ArgumentError, ValueError):
        raise SettingsError(f"环境变量 {DATABASE_URL} 不是数据库地址") from None
    return SecretStr(url.set(drivername=_DRIVER).render_as_string(hide_password=False))
