"""替身：实现 Agent 网关的 ModelBackend，不调用模型，按脚本给出固定的输出，见 stand-ins spec 和
add-stand-ins 的 design D7 到 D10。

每次请求都在给定的模型上一次成功，用量是 0；不看 model 和 timeout。
"""

from datetime import timedelta
from typing import Final

from tradingagentspro.contracts.agent_call import (
    Challenge,
    CriticReply,
    CriticRequest,
    DispatchTasks,
    PmReply,
    PmRequest,
    RetrospectiveReport,
    ReviewerRequest,
    SubAgentRequest,
    SubmitConclusion,
    SubmitReview,
    TaskResults,
)
from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.contracts.review import (
    CriticReview,
    PlanResponse,
    ResponseDecision,
    Verdict,
)
from tradingagentspro.contracts.task import ResultStatus
from tradingagentspro.integrations.standin.conclusions import (
    conclusion,
    research_requests,
    stand_in_signal,
)
from tradingagentspro.integrations.standin.config import StandInConfig
from tradingagentspro.modules.gateway import ModelReply
from tradingagentspro.shared.trading_calendar import TradingCalendar

_NO_USAGE: Final = TokenUsage(input_tokens=0, output_tokens=0)
_APPROVE: Final = SubmitReview(
    review=CriticReview(verdict=Verdict.APPROVE, rationale="替身一律通过，不做审核")
)
_REPORT: Final = RetrospectiveReport(summary="替身的复盘报告：复盘的内容由 M6 的 Reviewer 给出")
_REPLY = "替身维持方案，不做回应"


class StandInBackend:
    """满足 Agent 网关的 ModelBackend：PM、Critic、三个子 agent 和 Reviewer 的替身。"""

    def __init__(self, calendar: TradingCalendar, config: StandInConfig) -> None:
        self._calendar = calendar
        self._config = config

    def pm(self, model: ModelRef, timeout: timedelta, request: PmRequest) -> ModelReply[PmReply]:
        """PM 替身的研究：要派任务、对话里还没有派过时派一轮；否则提交按案件类型固定的结论，
        引用对话里全部完成的 Result。被退回重交时交回同样的结论。
        """
        snapshot, context = request.snapshot, request.context
        dispatched = any(isinstance(turn.reply, DispatchTasks) for turn in request.turns)
        requests = research_requests(snapshot, context)
        if requests and not dispatched:
            return ModelReply(DispatchTasks(requests=requests), _NO_USAGE)
        results = _completed(request)
        submitted = conclusion(context, snapshot, results, self._calendar, self._config)
        return ModelReply(SubmitConclusion(conclusion=submitted), _NO_USAGE)

    def pm_respond(
        self, model: ModelRef, timeout: timedelta, request: PmRequest
    ) -> ModelReply[PlanResponse]:
        """维持方案：逐条回应对话最后一轮的质询，不写方案条款。"""
        challenge = request.turns[-1].feedback
        if not isinstance(challenge, Challenge):
            raise ValueError("PM 回应时对话的最后一轮交回的必须是 Critic 的质询")
        replies = tuple(_REPLY for _ in challenge.review.challenges)
        response = PlanResponse(decision=ResponseDecision.MAINTAIN, replies=replies)
        return ModelReply(response, _NO_USAGE)

    def critic(
        self, model: ModelRef, timeout: timedelta, request: CriticRequest
    ) -> ModelReply[CriticReply]:
        """第一次审核就通过：不派补充研究，不写质询，不引用 Result。"""
        return ModelReply(_APPROVE, _NO_USAGE)

    def sub_agent(
        self, model: ModelRef, timeout: timedelta, request: SubAgentRequest
    ) -> ModelReply[str]:
        """替身的信号的 JSON 文本，引用 Task 的输入；输入为空时引用快照的市场状态条目。"""
        snapshot = request.snapshot
        cited = request.task.inputs or (snapshot.market.entry_id,)
        signal = stand_in_signal(snapshot, cited, self._config)
        return ModelReply(signal.model_dump_json(), _NO_USAGE)

    def reviewer(
        self, model: ModelRef, timeout: timedelta, request: ReviewerRequest
    ) -> ModelReply[RetrospectiveReport]:
        """固定的复盘报告。"""
        return ModelReply(_REPORT, _NO_USAGE)


def _completed(request: PmRequest) -> tuple[str, ...]:
    """对话里状态是完成的 Result 的 ID，按交回的先后、不重复。"""
    ids: dict[str, None] = {}
    for turn in request.turns:
        if isinstance(turn.feedback, TaskResults):
            for outcome in turn.feedback.outcomes:
                if outcome.result.status is ResultStatus.COMPLETED:
                    ids[str(outcome.result.task_id)] = None
    return tuple(ids)
