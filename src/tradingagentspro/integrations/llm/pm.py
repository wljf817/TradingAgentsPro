"""调用 PM 的消息和交回工具，见 pm-calls spec 和 add-pm-calls 的 design D2 到 D4。"""

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
from tradingagentspro.contracts.review import PlanResponse
from tradingagentspro.integrations.llm.client import ReplyTool
from tradingagentspro.modules.gateway import pm_context

_DISPATCH_TASKS: Final = "dispatch_tasks"
_SUBMIT_CONCLUSION: Final = "submit_conclusion"
_RESPOND_TO_CHALLENGE: Final = "respond_to_challenge"

RESEARCH_TOOLS: Final[tuple[ReplyTool[DispatchTasks | SubmitConclusion], ...]] = (
    ReplyTool(_DISPATCH_TASKS, "派发任务", DispatchTasks),
    ReplyTool(_SUBMIT_CONCLUSION, "提交结论", SubmitConclusion),
)
RESPONSE_TOOLS: Final[tuple[ReplyTool[PlanResponse], ...]] = (
    ReplyTool(_RESPOND_TO_CHALLENGE, "回应质询", PlanResponse),
)


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
