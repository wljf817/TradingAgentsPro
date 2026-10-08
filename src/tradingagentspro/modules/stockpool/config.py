"""股票池的阈值和默认值，含义见 stock-pool spec「股票池配置」。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.listing import Exchange, SecurityType


class StockPoolConfig(BaseModel):
    """股票池的阈值。只要求数值大于 0、允许的范围至少一项，不校验配置项之间的关系。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 候选和关注合计不超过这么多只，持仓不计入
    max_pool_size: int = Field(default=30, gt=0)
    max_watch: int = Field(default=8, gt=0)
    # 每个交易日通过的加入和删除合计
    max_daily_changes: int = Field(default=10, gt=0)
    # 成为候选或保持后，过这么多个交易日收盘时还是候选就移出
    candidate_ttl_sessions: int = Field(default=10, gt=0)
    # 移出后过这么多个交易日才能重新加入；至少 1 个交易日，当天的增删次数才能从记录推出
    readd_cooldown_sessions: int = Field(default=5, gt=0)
    # 日均成交额，美元
    min_average_dollar_volume: Decimal = Field(default=Decimal(10_000_000), gt=0)
    allowed_exchanges: frozenset[Exchange] = Field(
        default=frozenset({Exchange.NYSE, Exchange.NASDAQ}), min_length=1
    )
    allowed_security_types: frozenset[SecurityType] = Field(
        default=frozenset({SecurityType.COMMON_STOCK, SecurityType.ADR}), min_length=1
    )
