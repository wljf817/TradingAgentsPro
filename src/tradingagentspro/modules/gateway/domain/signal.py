"""收到输出时的校验：子 agent 的输出和 PM 结论里的信号，见 signal-format spec「收到输出时的校验」
「结论里的信号」。

格式错误由 contracts 的模型在构造时给出；这里只对已经构造好的信号检查依赖配置和"现在"的两项，
给出第一项不通过的原因。不通过和格式错误一样是格式不合法；这是模型输出的正常结果，所以作为返回值，
不抛异常。只在收到输出时检查：已经写入的信号读回时不再查，改配置不影响它们。
"""

from datetime import datetime
from enum import StrEnum

from tradingagentspro.contracts.conclusion import Conclusion, ExplorationConclusion
from tradingagentspro.contracts.signal import NoSignal, Signal, SubAgentOutput
from tradingagentspro.modules.gateway.config import GatewayConfig


class SignalRejection(StrEnum):
    """格式正确的信号不通过的原因，按 spec「收到输出时的校验」的顺序。"""

    HORIZON_NOT_OFFERED = "horizon_not_offered"  # 评估窗口不在配置的选项里
    DATA_AFTER_NOW = "data_after_now"  # 数据截至时刻晚于现在


def signal_rejection(
    output: SubAgentOutput, now: datetime, config: GatewayConfig
) -> SignalRejection | None:
    """给出第一项不通过的原因；无信号或者都通过时为 None。now 是收到输出的时刻。"""
    if isinstance(output, NoSignal):
        return None
    if output.horizon not in config.signal_horizons:
        return SignalRejection.HORIZON_NOT_OFFERED
    if output.data_as_of > now:
        return SignalRejection.DATA_AFTER_NOW
    return None


def conclusion_signal_rejection(
    conclusion: Conclusion, now: datetime, config: GatewayConfig
) -> SignalRejection | None:
    """按顺序检查 PM 结论里的每个信号，给出第一个不通过的原因；都通过时为 None。

    不通过时整份结论格式不合法，不能只丢掉信号、照常采用结论。now 是收到结论的时刻。
    """
    for signal in _signals(conclusion):
        rejection = signal_rejection(signal, now, config)
        if rejection is not None:
            return rejection
    return None


def _signals(conclusion: Conclusion) -> tuple[Signal, ...]:
    """持仓、关注、候选的结论一个；寻股按加入的顺序，每只候选一个。"""
    if isinstance(conclusion, ExplorationConclusion):
        return tuple(addition.signal for addition in conclusion.additions)
    return (conclusion.signal,)
