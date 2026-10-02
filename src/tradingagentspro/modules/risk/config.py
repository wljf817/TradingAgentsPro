"""风控的阈值和默认值，含义见 risk-rules spec「风控阈值配置」。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RiskConfig(BaseModel):
    """风控阈值。比例都是相对权益的小数；紧急阈值是跌幅比例。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 一只股票（加仓按合并后）的计划风险不超过权益乘以这个比例
    max_trade_risk_ratio: Decimal = Field(default=Decimal("0.01"), gt=0)
    # 组合合计风险不超过权益乘以这个比例
    max_total_risk_ratio: Decimal = Field(default=Decimal("0.05"), gt=0)
    # 一只股票的市值不超过权益乘以这个比例
    max_position_ratio: Decimal = Field(default=Decimal("0.20"), gt=0)
    # 一个板块的市值不超过权益乘以这个比例
    max_sector_ratio: Decimal = Field(default=Decimal("0.40"), gt=0)
    # 持仓和有开仓预留的股票合计不超过这么多只
    max_positions: int = Field(default=8, gt=0)
    # 费用和滑点缓冲 = 成交额乘以这个比例
    cost_buffer_ratio: Decimal = Field(default=Decimal("0.005"), gt=0)
    # 同一股票两次交易至少相隔这么多个交易日
    min_trade_interval_sessions: int = Field(default=1, gt=0)
    # 方案的紧急阈值必须在下限和上限之间（含两端），没填时用默认值
    emergency_threshold_min: Decimal = Field(default=Decimal("0.03"), gt=0)
    emergency_threshold_max: Decimal = Field(default=Decimal("0.20"), gt=0)
    emergency_threshold_default: Decimal = Field(default=Decimal("0.08"), gt=0)
