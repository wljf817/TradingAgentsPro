"""调用子 agent 的交回工具和注册项，见 sub-agent-calls spec 和 add-sub-agent-calls 的 design。"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from tradingagentspro.contracts.agent_call import SubAgentRequest
from tradingagentspro.contracts.signal import NoSignal, Signal
from tradingagentspro.integrations.llm.client import ReadTool, ReplyTool

REPLY_TOOLS: Final[tuple[ReplyTool[Signal | NoSignal], ...]] = (
    ReplyTool("submit_signal", "提交信号", Signal),
    ReplyTool("submit_no_signal", "提交无信号", NoSignal),
)

type RecordEntries = Callable[[Iterable[str]], None]  # 工具把自己返回的内容的 ID 记下来
type ToolBuilder = Callable[[SubAgentRequest, RecordEntries], Sequence[ReadTool]]


@dataclass(frozen=True, slots=True)
class SubAgentSetup:
    """一个接上真实模型的子 agent：系统指令，和给出这次运行的读取工具的函数。"""

    instructions: str
    tools: ToolBuilder


def no_tools(request: SubAgentRequest, record: RecordEntries) -> Sequence[ReadTool]:
    """还没有读取工具的子 agent。"""
    return ()
