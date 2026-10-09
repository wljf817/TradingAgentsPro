"""定时心跳的阈值和默认值，含义见 review-dates spec「复查日与催化剂的配置」。"""

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HeartbeatConfig(BaseModel):
    """定时心跳的阈值。三个复查间隔是同一个范围，要求最短 ≤ 默认 ≤ 最长。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 新设和续期的复查日在今天之后第 min 到第 max 个交易日之间；
    # 平时调整推后的复查日不晚于上次复查那天之后第 max 个交易日
    min_review_sessions: int = Field(default=3, ge=1)
    max_review_sessions: int = Field(default=20, ge=1)
    # 亏损的持仓续期时复查日不晚于今天之后第这么多个交易日；
    # 也写进 PM 的提示，作为一般情况下的复查间隔
    default_review_sessions: int = Field(default=5, ge=1)
    # 持仓的浮动盈亏比例低于它的相反数时，平时调整不能推后复查日，续期按默认间隔复查
    max_postpone_loss: Decimal = Field(default=Decimal("0.05"), gt=0, lt=1)
    # 引用日程项的催化剂，截止日期在日程项之后第 1 到第这么多个交易日
    max_schedule_lag_sessions: int = Field(default=2, ge=1)
    # 不引用日程项的催化剂，截止日期不晚于今天之后第这么多个交易日
    max_deadline_sessions: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def _check_review_range(self) -> Self:
        # 最短大于最长时任何复查日都通不过，每个复查案件都会重交到结论无效
        if self.min_review_sessions > self.default_review_sessions:
            raise ValueError("复查日的默认间隔不能小于最短间隔")
        if self.default_review_sessions > self.max_review_sessions:
            raise ValueError("复查日的默认间隔不能大于最长间隔")
        return self
