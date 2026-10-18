"""替身：按脚本代替 PM、Critic、三个子 agent 和 Reviewer，实现 Agent 网关的 ModelBackend，让案件流程
在接入模型以前就能在模拟盘里跑通。M5 用真实模型的适配器替换它。

见 openspec 的 stand-ins spec。装配时用 StandInBackend（交易日历和 StandInConfig）作为网关的
ModelBackend，用 stand_in_models() 作为网关的模型配置。
"""

from tradingagentspro.integrations.standin.backend import StandInBackend
from tradingagentspro.integrations.standin.config import StandInConfig, stand_in_models

__all__ = ["StandInBackend", "StandInConfig", "stand_in_models"]
