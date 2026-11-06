"""真实模型的后端：各 agent 的调用由它们各自的变更接上，见 add-model-adapter 的 design D9。"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Final

from tradingagentspro.contracts.agent_call import (
    CriticReply,
    CriticRequest,
    InvalidReply,
    PmReply,
    PmRequest,
    PmResponseReply,
    RetrospectiveReport,
    ReviewerRequest,
    SubAgentRequest,
)
from tradingagentspro.contracts.model import ModelRef
from tradingagentspro.integrations.llm.client import LlmClient, ReplyTool
from tradingagentspro.integrations.llm.errors import InvalidOutput
from tradingagentspro.integrations.llm.pm import RESEARCH_TOOLS, RESPONSE_TOOLS, pm_messages
from tradingagentspro.modules.gateway import ModelReply

_EMPTY_ANSWER: Final = "（空回答）"  # 厂商不收空的文字块，见 add-pm-calls 的 design D7


class LlmBackend:
    """满足 Agent 网关的 ModelBackend，用 LlmClient 问模型。"""

    def __init__(self, client: LlmClient, pm_instructions: str) -> None:
        self._client = client
        self._pm_instructions = pm_instructions

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
    ) -> ModelReply[str]:
        raise NotImplementedError("子 agent 还没有接上真实模型")

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
                history=pm_messages(request),
            )
        except InvalidOutput as invalid:
            text = invalid.text.strip() or _EMPTY_ANSWER
            return ModelReply(InvalidReply(text=text, reason=invalid.reason), invalid.usage)
        return ModelReply(reply.output, reply.usage, reply.served_model)
