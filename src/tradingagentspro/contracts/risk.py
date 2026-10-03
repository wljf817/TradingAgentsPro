"""风控结果：通过、调整或拒绝，以及原因。"""

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
    - violations：调整时是请求股数下不通过的规则，拒绝时是 1 股时不通过的规则，通过时为空
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RiskOutcome
    quantity: int | None
    emergency_threshold: Decimal | None
    expires_at: AwareDatetime | None
    violations: tuple[RiskViolation, ...]
