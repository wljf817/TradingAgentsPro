"""子 agent 的上下文：调用子 agent 时唯一的一条消息，转成 JSON 交给模型。

Agent 网关组出它，真实模型的后端读它。条目 ID 和数字都按快照的原样。见 sub-agent-calls spec
「子 agent 的上下文」和 add-sub-agent-calls 的 design D5。
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, SerializeAsAny

from tradingagentspro.contracts.signal import EntryId
from tradingagentspro.contracts.snapshot import (
    EventEntry,
    MarketEntry,
    QuoteEntry,
    SnapshotEntry,
)
from tradingagentspro.contracts.task import TaskType


class SubAgentContext(BaseModel):
    """子 agent 的上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_type: TaskType
    symbol: str
    question: str
    taken_at: AwareDatetime  # 快照时刻
    market: MarketEntry
    quote: QuoteEntry | None  # 这只股票的行情；快照里没有时为 None
    events: tuple[EventEntry, ...]
    # 声明成基类时只序列化基类的字段，SerializeAsAny 才带上条目的内容
    inputs: tuple[SerializeAsAny[SnapshotEntry], ...]  # Task 的输入在这版快照里查到的条目
    missing_inputs: tuple[EntryId, ...]  # Task 的输入在这版快照里查不到的 ID
