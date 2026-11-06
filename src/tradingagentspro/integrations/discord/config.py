"""Discord 适配器的配置。

见 discord-notification spec「Discord 配置」和 add-discord-notify 的 design D4；
提交超时见 manual-plans spec「提交接口」。
"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, SecretStr


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
