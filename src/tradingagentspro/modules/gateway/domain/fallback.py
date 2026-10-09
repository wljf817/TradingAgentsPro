"""备用模型：一个模型的熔断状态、一次调用的步骤、每次尝试的超时和 PM 用备用模型时的限制，见
model-fallback spec「什么算不可用」「尝试的超时」「切换顺序」「熔断」「切换的事件」「备用模型的
限制」。

网关不调用模型：一次调用由调用方驱动，网关只给出下一步向哪个模型尝试、最多等多久，调用方报告每次
尝试成功还是失败。熔断状态只在模型不可用和成功时变化；冷却中和冷却结束由"现在"和冷却结束的时刻
推出来，时间流逝不用推进。
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.conclusion import Conclusion, Decision, ExplorationConclusion
from tradingagentspro.contracts.model import ModelRef, ModelUse
from tradingagentspro.modules.gateway.config import GatewayConfig


class CallError(StrEnum):
    """调用错误，见 spec「什么算不可用」。M5 把调用异常归成其中一种；不分种类处理，只用于说明。"""

    TIMEOUT = "timeout"  # 超时
    CONNECTION = "connection"  # 连接失败
    RATE_LIMITED = "rate_limited"  # 限流
    QUOTA_EXHAUSTED = "quota_exhausted"  # 额度或余额用完：厂商返回的，不是本系统的调用预算
    AUTH_FAILED = "auth_failed"  # 认证失败
    SERVER_ERROR = "server_error"  # 服务端出错或过载
    OTHER = "other"  # 其他调用错误


# 说明里的原因：调用错误，冷却中被跳过时为 None
_REASONS: Final[Mapping[CallError | None, str]] = {
    CallError.TIMEOUT: "超时",
    CallError.CONNECTION: "连接失败",
    CallError.RATE_LIMITED: "限流",
    CallError.QUOTA_EXHAUSTED: "额度或余额用完",
    CallError.AUTH_FAILED: "认证失败",
    CallError.SERVER_ERROR: "服务端出错",
    CallError.OTHER: "其他调用错误",
    None: "冷却中",
}
_SUB_AGENTS: Final = frozenset({Agent.NEWS, Agent.QUANT, Agent.FUNDAMENTALS})


@dataclass(frozen=True, slots=True)
class Breaker:
    """一个模型的熔断状态。状态变化的方法都返回新的状态。

    - failures：上次成功以来连续不可用的时刻，只留最近"连续不可用次数"个
    - cooling_until：冷却结束的时刻；冷却结束以后、还没成功时也保留，这时再不可用就直接再冷却
    """

    failures: tuple[datetime, ...] = ()
    cooling_until: datetime | None = None

    def cooling(self, now: datetime) -> bool:
        """冷却中：现在早于冷却结束的时刻。"""
        return self.cooling_until is not None and now < self.cooling_until

    def succeeded(self) -> Self:
        """成功：不论在哪个状态，都回到正常、清零。"""
        return type(self)()

    def failed(self, now: datetime, config: GatewayConfig) -> Self:
        """在 now 不可用了一次。"""
        if self.cooling_until is not None:
            if now < self.cooling_until:
                return self  # 冷却中：不变
            return type(self)(cooling_until=now + config.breaker_cooldown)  # 冷却结束：再冷却
        failures = (*self.failures, now)[-config.breaker_failures :]
        if len(failures) == config.breaker_failures and now - failures[0] <= config.breaker_window:
            return type(self)(cooling_until=now + config.breaker_cooldown)
        return type(self)(failures=failures)


_NORMAL: Final = Breaker()


@dataclass(frozen=True, slots=True)
class Unavailable:
    """一次调用里不可用的一个模型。error 是最后一次尝试的调用错误；冷却中被跳过时为 None。"""

    model: ModelRef
    error: CallError | None


@dataclass(frozen=True, slots=True)
class ModelSwitch:
    """一次切换，调用方写进程序日志。error 和 Unavailable 的相同。"""

    case_id: UUID
    agent: Agent
    from_model: ModelRef
    to_model: ModelRef
    error: CallError | None
    at: datetime


@dataclass(frozen=True, slots=True)
class ModelCall:
    """一次调用进行到哪里：正在向 chain[index] 做第 attempt 次尝试。

    - deadline：子 agent 任务的截止时间；PM、Critic、Reviewer 没有
    - unavailable：这次调用里已经不可用的模型，按先后
    """

    case_id: UUID
    agent: Agent
    chain: tuple[ModelRef, ...]
    index: int
    attempt: int
    deadline: datetime | None
    unavailable: tuple[Unavailable, ...] = ()

    @property
    def model(self) -> ModelRef:
        """正在尝试的模型。"""
        return self.chain[self.index]


@dataclass(frozen=True, slots=True)
class Attempt:
    """下一步：向 call.model 发一次请求，最多等 timeout。switches 是走到这一步经过的切换。"""

    call: ModelCall
    timeout: timedelta
    switches: tuple[ModelSwitch, ...] = ()


@dataclass(frozen=True, slots=True)
class AllUnavailable:
    """起点和它后面的模型都不可用：调用失败，告警的依据。"""

    case_id: UUID
    agent: Agent
    unavailable: tuple[Unavailable, ...]
    switches: tuple[ModelSwitch, ...] = ()

    @property
    def detail(self) -> str:
        """调用失败的说明，比如"模型都不可用：anthropic/p-1 超时；anthropic/p-2 冷却中"。"""
        reasons = (f"{u.model} {_REASONS[u.error]}" for u in self.unavailable)
        return "模型都不可用：" + "；".join(reasons)


@dataclass(frozen=True, slots=True)
class DeadlineReached:
    """到了任务的截止时间：不再尝试，任务由 task-dispatch 记超时。不是全部不可用，不告警。"""

    call: ModelCall


type CallStep = Attempt | AllUnavailable | DeadlineReached


def start(
    case_id: UUID,
    agent: Agent,
    chain: tuple[ModelRef, ...],
    first: int,
    deadline: datetime | None,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
) -> CallStep:
    """一次调用的第一步：从起点 chain[first] 开始，跳过冷却中的模型。"""
    return _try_from(ModelCall(case_id, agent, chain, first, 1, deadline), breakers, now, config)


def exhausted(call: ModelCall, config: GatewayConfig) -> bool:
    """这次尝试是这个模型在这次调用里的最后一次：失败就不可用。"""
    return call.attempt > config.call_retries


def after_failure(
    call: ModelCall,
    error: CallError,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
) -> CallStep:
    """正在进行的尝试失败以后的下一步：到了截止时间就停止；还能重试就重试；否则换下一个模型。

    重试不看熔断状态；breakers 是已经算上这次不可用的熔断状态，换模型时用。
    """
    if call.deadline is not None and now >= call.deadline:
        return DeadlineReached(call)
    if not exhausted(call, config):
        retry = replace(call, attempt=call.attempt + 1)
        return Attempt(retry, attempt_timeout(retry, now, config))
    return _move_past(call, error, breakers, now, config, ())


def attempt_timeout(call: ModelCall, now: datetime, config: GatewayConfig) -> timedelta:
    """这次尝试的超时：子 agent 不超过离截止时间还剩的时间；PM、Critic、Reviewer 不看截止时间。"""
    if call.agent not in _SUB_AGENTS:
        return config.call_timeout
    limit = config.subagent_call_timeout
    return limit if call.deadline is None else min(limit, call.deadline - now)


def _try_from(
    call: ModelCall,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
    switches: tuple[ModelSwitch, ...] = (),
) -> CallStep:
    """向 call.model 尝试；到了截止时间就停止，它在冷却中就跳过、换下一个。"""
    if call.deadline is not None and now >= call.deadline:
        return DeadlineReached(call)
    if breakers.get(call.model, _NORMAL).cooling(now):
        return _move_past(call, None, breakers, now, config, switches)
    return Attempt(call, attempt_timeout(call, now, config), switches)


def _move_past(
    call: ModelCall,
    error: CallError | None,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
    switches: tuple[ModelSwitch, ...],
) -> CallStep:
    """call.model 在这次调用里不可用了（error 为 None 时是冷却中被跳过），换到下一个模型。"""
    gone = (*call.unavailable, Unavailable(call.model, error))
    if call.index + 1 == len(call.chain):
        return AllUnavailable(call.case_id, call.agent, gone, switches)
    following = replace(call, index=call.index + 1, attempt=1, unavailable=gone)
    switch = ModelSwitch(call.case_id, call.agent, call.model, following.model, error, now)
    return _try_from(following, breakers, now, config, (*switches, switch))


def blocked_decision(
    conclusion: Conclusion, use: ModelUse, config: GatewayConfig
) -> Decision | None:
    """提交结论的调用用的是备用模型、决定在不能提的决定里时，给出这个决定；否则为 None。

    寻股的结论没有决定，不限制。被限制时调用方按结论校验不通过退回 PM 重交。
    """
    if not use.fallback or isinstance(conclusion, ExplorationConclusion):
        return None
    decision = conclusion.decision
    return decision if decision in config.fallback_blocked_decisions else None
