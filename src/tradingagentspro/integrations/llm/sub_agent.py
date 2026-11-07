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

# 每个读取工具的说明都加上这一句，见 news-tools、filing-tools spec「工具的说明」和
# add-financial-figures 的 design D9
STOCK_IS_SET: Final = "股票和时刻由程序给出，参数里没有股票代码。"


@dataclass(frozen=True, slots=True)
class SubAgentSetup:
    """一个接上真实模型的子 agent：系统指令，和给出这次运行的读取工具的函数。"""

    instructions: str
    tools: ToolBuilder


def combined(*builders: ToolBuilder) -> ToolBuilder:
    """几组读取工具合在一起，按给出的顺序。"""

    def build(request: SubAgentRequest, record: RecordEntries) -> Sequence[ReadTool]:
        return tuple(tool for builder in builders for tool in builder(request, record))

    return build


def no_tools(request: SubAgentRequest, record: RecordEntries) -> Sequence[ReadTool]:
    """还没有读取工具的子 agent。"""
    return ()
