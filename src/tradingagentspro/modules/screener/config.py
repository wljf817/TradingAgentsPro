"""寻股粗筛的阈值和默认值，含义见 stock-screen spec「粗筛配置」。

允许的范围和流动性门槛不在这里，取股票池的配置，见 add-stock-screen 的 design D4。
"""

from datetime import timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ScreenerConfig(BaseModel):
    """粗筛的配置。只要求数值大于 0、比例在 0 和 1 之间，不校验配置项之间的关系。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 名单最多这么多只
    top_n: int = Field(default=10, gt=0)
    # 股票列表数据源只给市值不低于它的美股，美元
    min_market_cap: Decimal = Field(default=Decimal(2_000_000_000), gt=0)
    # 日均成交额按截止交易日及之前这么多个交易日平均
    dollar_volume_sessions: int = Field(default=20, gt=0)
    # 相对强度比较截止交易日和这么多个交易日以前的收盘价
    relative_strength_sessions: int = Field(default=126, gt=0)
    # 相对强度的基准
    benchmark: str = Field(default="SPY", min_length=1)
    # 相对强度比它高的合格股票少于合格股票数乘这个比例时，满足相对强度
    relative_strength_top: Decimal = Field(default=Decimal("0.2"), gt=0, lt=1)
    # 上升趋势：短均线高于长均线，收盘价高于长均线
    short_ma_sessions: int = Field(default=50, gt=0)
    long_ma_sessions: int = Field(default=200, gt=0)
    # 回调幅度：收盘价比这么多个交易日里最高的收盘价低多少
    pullback_sessions: int = Field(default=20, gt=0)
    min_pullback: Decimal = Field(default=Decimal("0.03"), gt=0, lt=1)
    max_pullback: Decimal = Field(default=Decimal("0.10"), gt=0, lt=1)
    # 量比：近期这么多个交易日的平均成交量除以再之前基期这么多个交易日的平均成交量
    volume_recent_sessions: int = Field(default=5, gt=0)
    volume_base_sessions: int = Field(default=50, gt=0)
    # 量比不低于它算成交量异常
    min_volume_ratio: Decimal = Field(default=Decimal("1.5"), gt=0)
    # 日线数据源只给这么久以前的数据；收盘后不满这么久时，当天的日线还不完整
    bar_delay: timedelta = Field(default=timedelta(minutes=15), gt=timedelta(0))
