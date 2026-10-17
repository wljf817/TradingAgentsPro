"""公司数据模块的配置和默认值，含义见 company-data spec「公司数据配置」。"""

from pydantic import BaseModel, ConfigDict, Field


class CompanyDataConfig(BaseModel):
    """公司数据模块的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 刷新财报日历时，取当前 UTC 日期往前这么多天以来的发布；免费档只给 1 个月的历史
    lookback_days: int = Field(default=30, gt=0)
    # 刷新财报日历时，取到当前 UTC 日期往后这么多天为止的发布；盖住最晚 60 个交易日以后的催化剂
    lookahead_days: int = Field(default=90, gt=0)
