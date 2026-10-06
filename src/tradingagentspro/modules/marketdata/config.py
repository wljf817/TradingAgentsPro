"""行情模块的配置和默认值，含义见 market-data spec「行情配置」。"""

from pydantic import BaseModel, ConfigDict, Field


class MarketDataConfig(BaseModel):
    """行情模块的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 用这只股票的行情更新时刻作为行情最近一次更新的时刻
    reference_symbol: str = Field(default="SPY", min_length=1)
