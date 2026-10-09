"""Agent 网关的阈值、默认值和各 agent 的模型，含义见 signal-format spec「信号的配置」、task-dispatch
spec「派发的配置」、model-fallback spec「模型的配置」「厂商校验」「备用模型的配置」。
"""

from collections.abc import Mapping
from datetime import timedelta
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


class ModelAssignments(BaseModel):
    """六个 agent 各自的模型，见 model-fallback spec「模型的配置」「厂商校验」。

    没有默认值：bootstrap 启动时从配置文件构造，报错就拒绝启动。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agents: Mapping[Agent, AgentModels]

    def of(self, agent: Agent) -> AgentModels:
        """这个 agent 的模型。"""
        return self.agents[agent]

    @model_validator(mode="after")
    def _check(self) -> Self:
        missing = set(Agent) - set(self.agents)
        if missing:
            raise ValueError(f"缺少这些 agent 的模型：{'、'.join(sorted(missing))}")
        shared = _vendors(self.agents[Agent.CRITIC]) & _vendors(self.agents[Agent.PM])
        if shared:
            raise ValueError(f"Critic 和 PM 不能用同一家厂商的模型：{'、'.join(sorted(shared))}")
        return self


def _vendors(models: AgentModels) -> set[str]:
    """一个 agent 的主模型和备用模型的厂商。"""
    return {model.vendor for model in models.chain}
