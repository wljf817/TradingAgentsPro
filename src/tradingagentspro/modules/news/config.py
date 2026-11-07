"""新闻工具和公告工具的阈值和默认值。"""

from pydantic import BaseModel, ConfigDict, PositiveInt


class NewsConfig(BaseModel):
    """新闻工具的阈值和默认值，含义见 news-tools spec「新闻工具的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # list_news 一次最多列多少条
    max_tool_rows: PositiveInt = 50
    # 不写起始日时往前多少天
    tool_default_days: PositiveInt = 7
    # 最早查到快照时刻往前多少天
    tool_max_days: PositiveInt = 90


class FilingConfig(BaseModel):
    """公告工具的阈值和默认值，含义见 filing-tools spec「公告工具的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # list_filings 一次最多列多少份
    max_filing_rows: PositiveInt = 50
    # list_filings 最早查到时刻 T 往前多少天，也是不写起始日时的范围
    filing_tool_days: PositiveInt = 365
    # read_filing 一次最多给多少字
    max_filing_chars: PositiveInt = 20000
    # read_filing 查找一次最多给多少处
    max_filing_matches: PositiveInt = 20
    # read_transcript 从结束往前找多少天
    transcript_window_days: PositiveInt = 100
    # read_transcript 最早查到时刻 T 往前多少天
    transcript_max_days: PositiveInt = 365
    # 文字稿的正文至少多少个字符
    min_transcript_chars: PositiveInt = 10000
