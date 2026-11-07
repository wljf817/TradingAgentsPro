"""真实模型的后端：各 agent 的调用由它们各自的变更接上，见 add-model-adapter 的 design D9。"""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Final

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.agent_call import (
    CriticReply,
    CriticRequest,
    InvalidReply,
    PmReply,
    PmRequest,
    PmResponseReply,
    RetrospectiveReport,
    ReviewerRequest,
    SubAgentReply,
    SubAgentRequest,
)
from tradingagentspro.contracts.model import ModelRef
from tradingagentspro.integrations.llm.client import LlmClient, ReplyTool
from tradingagentspro.integrations.llm.errors import InvalidOutput
from tradingagentspro.integrations.llm.pm import (
    RESEARCH_TOOLS,
    RESPONSE_TOOLS,
    history_tool,
    pm_messages,
)
from tradingagentspro.integrations.llm.sub_agent import REPLY_TOOLS, SubAgentSetup
from tradingagentspro.modules.gateway import (
    HistoryQueryService,
    ModelReply,
    sub_agent_context,
)

_EMPTY_ANSWER: Final = "（空回答）"  # 厂商不收空的文字块，见 add-pm-calls 的 design D7


class LlmBackend:
    """满足 Agent 网关的 ModelBackend，用 LlmClient 问模型。"""

    def __init__(
        self,
        client: LlmClient,
        pm_instructions: str,
        history: HistoryQueryService,
        max_history_queries: int,
        sub_agents: Mapping[SubAgent, SubAgentSetup],
        max_sub_agent_tool_calls: int,
    ) -> None:
        self._client = client
        self._pm_instructions = pm_instructions
        self._history = history
        self._max_history_queries = max_history_queries
        self._sub_agents = sub_agents
        self._max_sub_agent_tool_calls = max_sub_agent_tool_calls

    def pm(self, model: ModelRef, timeout: timedelta, request: PmRequest) -> ModelReply[PmReply]:
        return self._run_pm(model, timeout, request, RESEARCH_TOOLS)

    def pm_respond(
        self, model: ModelRef, timeout: timedelta, request: PmRequest
    ) -> ModelReply[PmResponseReply]:
        return self._run_pm(model, timeout, request, RESPONSE_TOOLS)

    def critic(
        self, model: ModelRef, timeout: timedelta, request: CriticRequest
    ) -> ModelReply[CriticReply]:
        raise NotImplementedError("Critic 还没有接上真实模型")

    def sub_agent(
        self, model: ModelRef, timeout: timedelta, request: SubAgentRequest
    ) -> ModelReply[SubAgentReply]:
        """运行一次；工具返回过的内容每次运行重新记，见 add-sub-agent-calls 的 design D3。"""
        target = request.task.target
        if target not in self._sub_agents:
            raise NotImplementedError(f"{target} 还没有接上真实模型")
        setup = self._sub_agents[target]
        returned: set[str] = set()
        try:
            reply = self._client.run(
                model,
                timeout,
                setup.instructions,
                sub_agent_context(request).model_dump_json(),
                REPLY_TOOLS,
                tools=setup.tools(request, returned.update),
                tool_limit=self._max_sub_agent_tool_calls,
            )
        except InvalidOutput as invalid:
            return ModelReply(SubAgentReply(output=_invalid_reply(invalid)), invalid.usage)
        output = SubAgentReply(output=reply.output, tool_entries=frozenset(returned))
        return ModelReply(output, reply.usage, reply.served_model)

    def reviewer(
        self, model: ModelRef, timeout: timedelta, request: ReviewerRequest
    ) -> ModelReply[RetrospectiveReport]:
        raise NotImplementedError("Reviewer 还没有接上真实模型")

    def _run_pm[T](
        self,
        model: ModelRef,
        timeout: timedelta,
        request: PmRequest,
        replies: Sequence[ReplyTool[T]],
    ) -> ModelReply[T | InvalidReply]:
        """向模型运行一次；输出不合法是一种回复，见 add-pm-calls 的 design D1。"""
        try:
            reply = self._client.run(
                model,
                timeout,
                self._pm_instructions,
                None,
                replies,
                tools=(history_tool(self._history, request.snapshot),),
                tool_limit=self._max_history_queries,
                history=pm_messages(request),
            )
        except InvalidOutput as invalid:
            return ModelReply(_invalid_reply(invalid), invalid.usage)
        return ModelReply(reply.output, reply.usage, reply.served_model)


def _invalid_reply(invalid: InvalidOutput) -> InvalidReply:
    return InvalidReply(text=invalid.text.strip() or _EMPTY_ANSWER, reason=invalid.reason)
