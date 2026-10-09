"""Agent 网关：agent 之间只通过它转发的 Task 和 Result 协作。现在只有收到模型输出时的校验：子 agent
的输出和 PM 结论里的信号，评估窗口是否在配置的选项里、数据截至时刻是否晚于现在。

网关不做交易判断、不下单。规则见 openspec 的 signal-format spec。模块之外的代码只能 import 这里
导出的名字。
"""

from tradingagentspro.modules.gateway.config import GatewayConfig
from tradingagentspro.modules.gateway.domain.signal import (
    SignalRejection,
    conclusion_signal_rejection,
    signal_rejection,
)

__all__ = [
    "GatewayConfig",
    "SignalRejection",
    "conclusion_signal_rejection",
    "signal_rejection",
]
