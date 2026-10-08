"""通知模块的配置和默认值，含义见 discord-notification spec「确认请求和截止时刻」。"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class NotificationConfig(BaseModel):
    """通知模块的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 请求确认以后多久没有结果算超时
    confirmation_timeout: timedelta = Field(default=timedelta(minutes=30), gt=timedelta(0))
