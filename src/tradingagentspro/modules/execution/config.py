"""执行模块的阈值和默认值，含义见 order-lifecycle spec「执行配置」。"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class ExecutionConfig(BaseModel):
    """执行模块的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 订单变为状态未知之后，等这么久才再查询券商
    unknown_lookup_delay: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
