"""子 agent 的身份：PM 和 Critic 只能向它们派任务，Result 记下出自哪一个。"""

from enum import StrEnum


class SubAgent(StrEnum):
    """子 agent 的身份。"""

    NEWS = "news"
    QUANT = "quant"
    FUNDAMENTALS = "fundamentals"
