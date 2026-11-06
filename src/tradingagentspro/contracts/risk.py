"""风控结果：通过、调整或拒绝，以及原因；一只股票的可用额度。"""

from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict


class RiskOutcome(StrEnum):
    PASSED = "passed"
    ADJUSTED = "adjusted"
    REJECTED = "rejected"


class RiskViolation(BaseModel):
    """一条规则不通过。rule 是规则名称，和 risk-rules spec 一致；message 是带具体数字的说明。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str
    message: str


class RiskDecision(BaseModel):
    """风控对一个方案的结果。

    - quantity：最终股数；拒绝时为 None
    - emergency_threshold：生效的紧急阈值；只有开仓类方案通过或调整时才有
    - expires_at：生效的有效期；通过或调整时才有，四种动作都有
    - violations：调整时是请求股数下不通过的规则，拒绝时是 1 股时不通过的规则，通过时为空；
      开仓方案因为佣金占比被拒绝时，是请求股数下不通过的规则加上「佣金占比」
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RiskOutcome
    quantity: int | None
    emergency_threshold: Decimal | None
    expires_at: AwareDatetime | None
    violations: tuple[RiskViolation, ...]


class Headroom(BaseModel):
    """一只股票的可用额度，见 case-headroom spec「可用额度的内容」。金额都是美元，舍入到分。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    available_cash: Decimal  # 可用资金：现金还能买的最大成交额
    open_slots: int | None  # 持仓名额：还能新建仓几只；这只股票有持仓时没有
    position_room: Decimal  # 单只仓位上限：这只股票还能买的成交额
    risk_limit: Decimal  # 单笔风险上限：买入以后这只股票合并计算的计划风险的上限
    min_order_value: Decimal  # 最小下单额：往返佣金占比不超标的最小成交额
