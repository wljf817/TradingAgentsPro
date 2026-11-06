"""按厂商分派：每个 agent 可以单独用替身或真实模型，见 model-adapter spec「按厂商分派」和
add-model-adapter 的 design D9。
"""

from collections.abc import Mapping
from datetime import timedelta

from tradingagentspro.contracts.agent_call import (
    CriticReply,
    CriticRequest,
    PmReply,
    PmRequest,
    PmResponseReply,
    RetrospectiveReport,
    ReviewerRequest,
    SubAgentRequest,
)
from tradingagentspro.contracts.model import ModelRef
from tradingagentspro.modules.gateway import ModelBackend, ModelReply


class VendorRouter:
    """满足 Agent 网关的 ModelBackend：按模型的厂商转发，结果和错误原样交回。"""

    def __init__(self, backends: Mapping[str, ModelBackend]) -> None:
        self._backends = backends

    def pm(self, model: ModelRef, timeout: timedelta, request: PmRequest) -> ModelReply[PmReply]:
        return self._backends[model.vendor].pm(model, timeout, request)

    def pm_respond(
        self, model: ModelRef, timeout: timedelta, request: PmRequest
    ) -> ModelReply[PmResponseReply]:
        return self._backends[model.vendor].pm_respond(model, timeout, request)

    def critic(
        self, model: ModelRef, timeout: timedelta, request: CriticRequest
    ) -> ModelReply[CriticReply]:
        return self._backends[model.vendor].critic(model, timeout, request)

    def sub_agent(
        self, model: ModelRef, timeout: timedelta, request: SubAgentRequest
    ) -> ModelReply[str]:
        return self._backends[model.vendor].sub_agent(model, timeout, request)

    def reviewer(
        self, model: ModelRef, timeout: timedelta, request: ReviewerRequest
    ) -> ModelReply[RetrospectiveReport]:
        return self._backends[model.vendor].reviewer(model, timeout, request)
