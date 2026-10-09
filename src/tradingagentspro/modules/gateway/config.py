"""Agent 网关的阈值、默认值、各 agent 的模型和模型的单价，含义见 signal-format spec「信号的配置」、
task-dispatch spec「派发的配置」、model-fallback spec「模型的配置」「厂商校验」「备用模型的配置」、
call-budget spec「模型的单价」「预算的配置」。
"""

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.conclusion import Decision
from tradingagentspro.contracts.model import ModelRef


class GatewayConfig(BaseModel):
    """Agent 网关的阈值。配置项之间只有一项约束：子 agent 的主模型和第一个备用模型的全部尝试放得进
    任务时限。

    三个预算的额度见 call-budget spec「预算的配置」：都是每个交易日多少美元，必须大于 0。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 子 agent 和 PM 出信号时可以选的评估窗口，交易日数；至少一个，否则任何信号都不合法
    signal_horizons: tuple[PositiveInt, ...] = Field(default=(3, 5, 20, 60), min_length=1)
    # 每个案件 PM 派发出去的任务最多这么多个
    max_pm_tasks: PositiveInt = 6
    # 每个案件 PM 最多派这么多轮
    max_pm_rounds: PositiveInt = 2
    # 每个案件 Critic 派发出去的任务最多这么多个，不占 PM 的任务数和轮次
    max_critic_tasks: PositiveInt = 2
    # 截止时间是派发时刻加这么久
    task_timeout: timedelta = Field(default=timedelta(minutes=5), gt=timedelta(0))

    # 一个模型在一次调用里失败以后最多再试这么多次
    call_retries: NonNegativeInt = 1
    # News、Quant、Fundamentals 每次尝试最多等这么久，并且不超过离任务截止时间还剩的时间
    subagent_call_timeout: timedelta = Field(default=timedelta(seconds=60), gt=timedelta(0))
    # PM、Critic、Reviewer 每次尝试最多等这么久
    call_timeout: timedelta = Field(default=timedelta(seconds=180), gt=timedelta(0))
    # 最近连续这么多次不可用，最早一次到最后一次不超过时间窗口，就冷却
    breaker_failures: PositiveInt = 3
    breaker_window: timedelta = Field(default=timedelta(minutes=10), gt=timedelta(0))
    breaker_cooldown: timedelta = Field(default=timedelta(minutes=5), gt=timedelta(0))
    # PM 用备用模型时不能提的决定；为空时不限制
    fallback_blocked_decisions: frozenset[Decision] = frozenset(
        {Decision.BUY, Decision.ADD, Decision.PROMOTE}
    )

    # 三个预算的额度：每个交易日记到这个预算的费用到这么多美元就用完
    general_budget: Decimal = Field(default=Decimal("2.00"), gt=0)
    reserve_budget: Decimal = Field(default=Decimal("1.00"), gt=0)
    exploration_budget: Decimal = Field(default=Decimal("0.50"), gt=0)

    @model_validator(mode="after")
    def _check_fallback_fits(self) -> Self:
        if (self.call_retries + 1) * self.subagent_call_timeout * 2 > self.task_timeout:
            raise ValueError("子 agent 的主模型和第一个备用模型的全部尝试放不进任务时限")
        return self


class AgentModels(BaseModel):
    """一个 agent 的主模型和按顺序的备用模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: ModelRef
    fallbacks: tuple[ModelRef, ...] = ()

    @property
    def chain(self) -> tuple[ModelRef, ...]:
        """按尝试的顺序：主模型在前，备用模型按配置的顺序。"""
        return (self.primary, *self.fallbacks)

    @model_validator(mode="after")
    def _check_unique(self) -> Self:
        if len(set(self.chain)) != len(self.chain):
            raise ValueError("同一个 agent 的主模型和备用模型不能重复")
        return self


class ModelPrice(BaseModel):
    """一个模型的单价：每百万 token 多少美元。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: ModelRef
    input_price: Decimal = Field(ge=0)
    output_price: Decimal = Field(ge=0)


class ModelAssignments(BaseModel):
    """六个 agent 各自的模型和模型的单价，见 model-fallback spec「模型的配置」「厂商校验」和
    call-budget spec「模型的单价」。

    没有默认值：bootstrap 启动时从配置文件构造，报错就拒绝启动。任何 agent 用到的模型都要有单价，
    同一个模型只配一份；没有 agent 用到的模型配了单价不报错。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agents: Mapping[Agent, AgentModels]
    prices: tuple[ModelPrice, ...]

    def of(self, agent: Agent) -> AgentModels:
        """这个 agent 的模型。"""
        return self.agents[agent]

    def price(self, model: ModelRef) -> ModelPrice:
        """这个模型的单价；没有配时抛 KeyError。"""
        for price in self.prices:
            if price.model == model:
                return price
        raise KeyError(model)

    @model_validator(mode="after")
    def _check(self) -> Self:
        missing = set(Agent) - set(self.agents)
        if missing:
            raise ValueError(f"缺少这些 agent 的模型：{'、'.join(sorted(missing))}")
        shared = _vendors(self.agents[Agent.CRITIC]) & _vendors(self.agents[Agent.PM])
        if shared:
            raise ValueError(f"Critic 和 PM 不能用同一家厂商的模型：{'、'.join(sorted(shared))}")
        priced = [price.model for price in self.prices]
        if len(set(priced)) != len(priced):
            raise ValueError("同一个模型不能配两份单价")
        used = {model for models in self.agents.values() for model in models.chain}
        unpriced = sorted(str(model) for model in used - set(priced))
        if unpriced:
            raise ValueError(f"这些模型没有单价：{'、'.join(unpriced)}")
        return self


def _vendors(models: AgentModels) -> set[str]:
    """一个 agent 的主模型和备用模型的厂商。"""
    return {model.vendor for model in models.chain}
