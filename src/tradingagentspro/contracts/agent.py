"""agent 的身份。

- SubAgent：子 agent，PM 和 Critic 只能向它们派任务，Result 记下出自哪一个
- Agent：调用模型的六个 agent，每个各配一套模型（model-fallback spec「模型的配置」）
"""

from enum import StrEnum


class SubAgent(StrEnum):
    """子 agent 的身份。"""

    NEWS = "news"
    QUANT = "quant"
    FUNDAMENTALS = "fundamentals"


class Agent(StrEnum):
    """调用模型的 agent，每个各配一套模型。子 agent 的值和 SubAgent 相同。"""

    PM = "pm"
    NEWS = "news"
    QUANT = "quant"
    FUNDAMENTALS = "fundamentals"
    CRITIC = "critic"
    REVIEWER = "reviewer"
