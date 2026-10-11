"""通知模块的配置和默认值，含义见 discord-notification spec「确认请求和截止时刻」和
confirmation-rules spec「确认规则的配置」。"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class NotificationConfig(BaseModel):
    """通知模块的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 请求确认以后多久没有结果算超时
    confirmation_timeout: timedelta = Field(default=timedelta(minutes=30), gt=timedelta(0))
    # 模型退出自动执行：打开时，案件里 Critic 通过、不处于降级模式的退出类方案不经人工确认
    auto_execute_model_exits: bool = False
    # 紧急退出要确认：打开时，个股跌幅和组合熔断触发的清仓先请求人工确认
    confirm_emergency_exits: bool = False
