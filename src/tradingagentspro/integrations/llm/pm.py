"""调用 PM 的消息、交回工具和读取工具，见 pm-calls spec、add-pm-calls 的 design D2 到 D4。"""

from datetime import date
from typing import Final

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tradingagentspro.contracts.agent_call import (
    DispatchTasks,
    InvalidReply,
    PmRequest,
    PmTurn,
    SubmitConclusion,
)
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.review import PlanResponse
from tradingagentspro.contracts.snapshot import Snapshot
from tradingagentspro.integrations.llm.client import ReadTool, ReplyTool
from tradingagentspro.modules.gateway import HistoryQueryService, pm_context

_DISPATCH_TASKS: Final = "dispatch_tasks"
_SUBMIT_CONCLUSION: Final = "submit_conclusion"
_RESPOND_TO_CHALLENGE: Final = "respond_to_challenge"
_QUERY_HISTORY: Final = "query_history"

RESEARCH_TOOLS: Final[tuple[ReplyTool[DispatchTasks | SubmitConclusion], ...]] = (
    ReplyTool(_DISPATCH_TASKS, "派发任务", DispatchTasks),
    ReplyTool(_SUBMIT_CONCLUSION, "提交结论", SubmitConclusion),
)
RESPONSE_TOOLS: Final[tuple[ReplyTool[PlanResponse], ...]] = (
    ReplyTool(_RESPOND_TO_CHALLENGE, "回应质询", PlanResponse),
)


def history_tool(history: HistoryQueryService, snapshot: Snapshot) -> ReadTool:
    """这次调用的 query_history：内容截到这次调用的快照的生成时刻，见 add-query-history 的 design
    D4、D12。
    """

    def query(symbol: str, since: date | None = None, case_type: CaseType | None = None) -> str:
        return history.query(
            snapshot.case_id, snapshot.taken_at, symbol, since, case_type
        ).model_dump_json()

    return ReadTool(_QUERY_HISTORY, "查询一只股票的历史案件和交易", query)


def pm_messages(request: PmRequest) -> list[ModelMessage]:
    """发给模型的全部消息：第一条是 PM 的上下文，之后是对话的每一轮。"""
    context = pm_context(request).model_dump_json()
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(context)])]
    for number, turn in enumerate(request.turns):
        messages += _turn_messages(number, turn)
    return messages


def _tool_name(reply: DispatchTasks | SubmitConclusion | PlanResponse) -> str:
    match reply:
        case DispatchTasks():
            return _DISPATCH_TASKS
        case SubmitConclusion():
            return _SUBMIT_CONCLUSION
        case PlanResponse():
            return _RESPOND_TO_CHALLENGE


def _turn_messages(number: int, turn: PmTurn) -> list[ModelMessage]:
    """一轮对话的模型回答和交回给模型的内容，规则见 pm-calls spec「对话变成消息」。"""
    feedback = turn.feedback.model_dump_json()
    if isinstance(turn.reply, InvalidReply):
        return [
            ModelResponse(parts=[TextPart(turn.reply.text)]),
            ModelRequest(parts=[UserPromptPart(feedback)]),
        ]
    name = _tool_name(turn.reply)
    call_id = f"pm_{number}"
    return [
        ModelResponse(
            parts=[ToolCallPart(name, turn.reply.model_dump_json(), tool_call_id=call_id)]
        ),
        ModelRequest(parts=[ToolReturnPart(name, feedback, tool_call_id=call_id)]),
    ]
