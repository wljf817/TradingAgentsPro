"""调用记录：向真实模型的一次尝试留下的记录，见 model-call-trace spec「调用记录」。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.modules.gateway.domain.fallback import CallError


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    """一次尝试的调用记录，写入以后不改。"""

    case_id: UUID
    task_id: UUID | None  # 子 agent 的任务才有
    agent: Agent
    model: ModelRef
    served_model: str | None  # 回答里写的模型名；没有拿到时为 None
    snapshot_version: int
    started_at: datetime
    ended_at: datetime
    usage: TokenUsage | None  # 各请求之和；拿不到时为 None
    error: CallError | None  # 没有拿到输出时的调用错误
    transcript: str  # 这次尝试的全部消息，见 add-model-call-trace 的 design D4
