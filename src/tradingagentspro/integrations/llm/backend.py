"""真实模型的后端：各 agent 的调用由它们各自的变更接上，见 add-model-adapter 的 design D9。"""

from datetime import timedelta

from tradingagentspro.contracts.agent_call import (
    CriticReply,
    CriticRequest,
    PmReply,
    PmRequest,
    RetrospectiveReport,
    ReviewerRequest,
    SubAgentRequest,
)
from tradingagentspro.contracts.model import ModelRef
from tradingagentspro.contracts.review import PlanResponse
from tradingagentspro.integrations.llm.client import LlmClient
from tradingagentspro.modules.gateway import ModelReply


class LlmBackend:
    """满足 Agent 网关的 ModelBackend，用 LlmClient 问模型。"""

    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def pm(self, model: ModelRef, timeout: timedelta, request: PmRequest) -> ModelReply[PmReply]:
        raise NotImplementedError("PM 还没有接上真实模型")

    def pm_respond(
        self, model: ModelRef, timeout: timedelta, request: PmRequest
    ) -> ModelReply[PlanResponse]:
        raise NotImplementedError("PM 还没有接上真实模型")

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
