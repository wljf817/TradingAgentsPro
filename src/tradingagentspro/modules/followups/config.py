"""跟进审批的阈值和默认值，含义见 follow-ups spec「跟进的配置」。"""

from pydantic import BaseModel, ConfigDict, Field


class FollowUpConfig(BaseModel):
    """跟进审批的阈值。各项至少为 1，配置项之间没有必须满足的关系。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每只股票待执行的跟进不超过这么多个，替换不算新增
    max_pending_per_stock: int = Field(default=2, ge=1)
    # 触发时间早于今天之后第这么多个交易日的收盘时刻
    max_trigger_sessions: int = Field(default=60, ge=1)
    # 每个交易日获批的申请（新增和替换）不超过这么多次
    max_daily_requests: int = Field(default=5, ge=1)
