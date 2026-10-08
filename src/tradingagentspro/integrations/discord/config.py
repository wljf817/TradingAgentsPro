"""Discord 适配器的配置。

见 discord-notification spec「Discord 配置」和 add-discord-notify 的 design D4；
提交超时见 manual-plans spec「提交接口」。
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr

TOKEN_VAR = "DISCORD_BOT_TOKEN"
# 配置项和对应的环境变量；ID 都是正整数
ID_VARS = {
    "guild_id": "DISCORD_GUILD_ID",
    "channel_id": "DISCORD_CHANNEL_ID",
    "user_id": "DISCORD_USER_ID",
}

_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*")


class DiscordConfig(BaseModel):
    """Discord 机器人的配置。token 是 SecretStr：字符串形式和错误信息里都是 **********。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: SecretStr = Field(min_length=1)
    guild_id: int = Field(gt=0)
    # 告警和确认都发到这个频道
    channel_id: int = Field(gt=0)
    # 只接受这个用户按按钮，告警和确认都 @ 他
    user_id: int = Field(gt=0)
    # 启动时等多久连上 Discord
    connect_timeout: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
    # 每次同步调用最多等多久
    send_timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))
    # /plan 等提交接口返回最多等多久；Discord 只在应答后 15 分钟内接受回复，见 manual-plans spec
    submit_timeout: timedelta = Field(
        default=timedelta(seconds=60), gt=timedelta(0), lt=timedelta(minutes=15)
    )

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """从 DISCORD_BOT_TOKEN、DISCORD_GUILD_ID、DISCORD_CHANNEL_ID、DISCORD_USER_ID 读取。

        没有或为空都算缺少，一次列出全部缺少的变量；ID 不是正整数时写明变量名。错误信息里
        只有变量名，没有 token。
        """
        names = (TOKEN_VAR, *ID_VARS.values())
        missing = [name for name in names if not environ.get(name)]
        if missing:
            raise ValueError(f"缺少环境变量：{'、'.join(missing)}")
        ids = {field: _positive_integer(environ, name) for field, name in ID_VARS.items()}
        return cls(
            token=SecretStr(environ[TOKEN_VAR]),
            guild_id=ids["guild_id"],
            channel_id=ids["channel_id"],
            user_id=ids["user_id"],
        )


def _positive_integer(environ: Mapping[str, str], name: str) -> int:
    text = environ[name]
    if not _POSITIVE_INTEGER.fullmatch(text):
        raise ValueError(f"环境变量 {name} 必须是正整数")
    return int(text)
