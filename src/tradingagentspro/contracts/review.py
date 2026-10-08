"""Critic 的审核和 PM 对质询的回应。

两者都来自模型输出，所以在这里校验格式，见 case-stages spec「Critic 审核和 PM 回应的格式」。
格式由 Agent 网关校验输出时挡住，调度器只收到合法的审核和回应。
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tradingagentspro.contracts.conclusion import PlanDraft
from tradingagentspro.contracts.trade_plan import TradeAction, check_plan_fields

type _Text = Annotated[str, StringConstraints(min_length=1)]


class Verdict(StrEnum):
    """Critic 的结论。补充研究是审核过程中经 Agent 网关派发的任务，不是结论。"""

    APPROVE = "approve"  # 通过
    CHALLENGE = "challenge"  # 质询；对退出类方案就是有异议
    VETO = "veto"  # 否决；只对开仓类方案有效


class CriticReview(BaseModel):
    """Critic 对一个方案的一次审核。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    verdict: Verdict
    rationale: str = Field(min_length=1)
    challenges: tuple[_Text, ...] = ()  # 逐条的质询，只有质询写
    cited_results: tuple[str, ...] = ()  # 引用的 Result ID，包括补充研究的

    @model_validator(mode="after")
    def _check_challenges(self) -> Self:
        if (self.verdict is Verdict.CHALLENGE) != bool(self.challenges):
            raise ValueError("质询必须逐条写出问题，通过和否决不写")
        return self


class ResponseDecision(StrEnum):
    """PM 对一轮质询的决定。"""

    MAINTAIN = "maintain"  # 维持
    MODIFY = "modify"  # 修改方案条款
    WITHDRAW = "withdraw"  # 撤回


class PlanResponse(BaseModel):
    """PM 对一轮质询的回应。只有开仓类方案会被质询，修改后的方案条款按开仓方案检查。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    decision: ResponseDecision
    replies: tuple[_Text, ...] = Field(min_length=1)  # 逐条回应，按质询的顺序
    plan: PlanDraft | None = None  # 修改后的方案条款，只有修改写

    @model_validator(mode="after")
    def _check_plan(self) -> Self:
        if (self.decision is ResponseDecision.MODIFY) != (self.plan is not None):
            raise ValueError("修改必须写新的方案条款，维持和撤回不写")
        if self.plan is not None:
            check_plan_fields(TradeAction.BUY, self.plan)  # 买入和加仓的条款规则相同
            if self.plan.emergency_threshold is None:
                raise ValueError("买入和加仓必须写紧急阈值")
        return self
