"""新闻工具的阈值和默认值，含义见 news-tools spec「新闻工具的配置」。"""

from pydantic import BaseModel, ConfigDict, PositiveInt


class NewsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # list_news 一次最多列多少条
    max_tool_rows: PositiveInt = 50
    # 不写起始日时往前多少天
    tool_default_days: PositiveInt = 7
    # 最早查到快照时刻往前多少天
    tool_max_days: PositiveInt = 90
