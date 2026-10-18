"""调用 agent 的输入和输出：Agent 网关交给 PM、Critic、子 agent、Reviewer 什么，它们交回什么，以及
PM 和 Critic 在一个案件里的对话。

网关的调用端口、替身和 M5 的模型适配器、案件工作流都用这些格式，所以放在 contracts。见 agent-calls
spec 和 add-stand-ins 的 design D2、D3。

对话的每一轮是 agent 的一次回复和交回给它的结果，就是一次工具调用和它的工具结果：PM 的回复是
dispatch_tasks、submit_conclusion 和对质询的回应，Critic 的回复是补充研究和审核。
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.conclusion import (
    Conclusion,
    FollowUpRequest,
    HoldingConclusion,
    WatchConclusion,
)
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.review import CriticReview, PlanResponse, Verdict
from tradingagentspro.contracts.snapshot import Snapshot
from tradingagentspro.contracts.task import Result, Task, TaskRequest
from tradingagentspro.contracts.thesis import InvestmentThesis, WatchThesis


class RecentCase(BaseModel):
    """这只股票最近结束的一个案件，放进案件上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_type: CaseType
    ended_at: AwareDatetime
    end_reason: CaseEndReason
    conclusion: Conclusion | None  # 通过校验的结论；没有结论的为 None


class CaseContext(BaseModel):
    """案件上下文：快照以外 PM 和 Critic 要看的、由调用方给出的内容，见 agent-calls spec。

    案件 ID、案件类型和股票在快照里。不适用的项为 None，没有的列表为空。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1)  # 案件要回答的问题；跟进是申请时写下的问题
    event: EventCategory | None  # 事件案件的事件类别
    required_agent: SubAgent | None  # 事件案件至少要派给的子 agent
    watch_thesis: WatchThesis | None  # 关注股现在的关注论点
    investment_thesis: InvestmentThesis | None  # 持仓现在的投资论点
    recent_cases: tuple[RecentCase, ...] = ()  # 这只股票最近结束的案件，按结束时刻从晚到早
    pending_follow_ups: tuple[FollowUpRequest, ...] = ()  # 这只股票待执行的跟进，按触发时间


class TaskOutcome(BaseModel):
    """一个任务请求的处理结果：派发、被拒绝或复用的 Task，和它的 Result。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: Task
    result: Result


class DispatchTasks(BaseModel):
    """dispatch_tasks：PM 或 Critic 派发一个或多个任务。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requests: tuple[TaskRequest, ...] = Field(min_length=1)


class SubmitConclusion(BaseModel):
    """submit_conclusion：PM 提交结论。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conclusion: Conclusion


class SubmitReview(BaseModel):
    """Critic 的一次审核。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review: CriticReview


type PmReply = DispatchTasks | SubmitConclusion  # PM 研究时的回复
type CriticReply = DispatchTasks | SubmitReview  # Critic 的回复


class TaskResults(BaseModel):
    """派发任务交回的结果：每个请求一个，按请求的顺序。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: tuple[TaskOutcome, ...] = Field(min_length=1)


class Returned(BaseModel):
    """结论校验不通过，退回 PM 重交的原因。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1)


class Challenge(BaseModel):
    """Critic 的一次质询，和这次审核里补充研究的 Task 和 Result。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review: CriticReview  # 结论是质询
    research: tuple[TaskOutcome, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.review.verdict is not Verdict.CHALLENGE:
            raise ValueError("交给 PM 回应的审核必须是质询")
        return self


def _check_dispatch(reply: DispatchTasks, feedback: object) -> None:
    """派发任务交回的是 Task 和 Result，和请求一样多。"""
    if not isinstance(feedback, TaskResults):
        raise ValueError("派发任务交回的必须是每个请求的 Task 和 Result")
    if len(feedback.outcomes) != len(reply.requests):
        raise ValueError("派发任务交回的结果必须和请求一样多")


class PmTurn(BaseModel):
    """PM 对话的一轮。回复和交回的结果怎样对应见 agent-calls spec「对话的每一轮」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reply: DispatchTasks | SubmitConclusion | PlanResponse
    feedback: TaskResults | Returned | Challenge

    @model_validator(mode="after")
    def _check(self) -> Self:
        match self.reply:
            case DispatchTasks():
                _check_dispatch(self.reply, self.feedback)
            case SubmitConclusion():
                if isinstance(self.feedback, TaskResults):
                    raise ValueError("提交结论交回的是退回的原因或 Critic 的质询")
            case PlanResponse():
                if not isinstance(self.feedback, Challenge):
                    raise ValueError("回应交回的是 Critic 下一次审核的质询")
        return self


class CriticTurn(BaseModel):
    """Critic 对话的一轮：补充研究交回 Task 和 Result，质询交回 PM 的回应。

    通过和否决以后没有下一次调用，不进对话。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reply: DispatchTasks | SubmitReview
    feedback: TaskResults | PlanResponse

    @model_validator(mode="after")
    def _check(self) -> Self:
        match self.reply:
            case DispatchTasks():
                _check_dispatch(self.reply, self.feedback)
            case SubmitReview():
                if self.reply.review.verdict is not Verdict.CHALLENGE:
                    raise ValueError("Critic 的通过和否决不进对话")
                if not isinstance(self.feedback, PlanResponse):
                    raise ValueError("质询交回的是 PM 的回应")
        return self


class PmRequest(BaseModel):
    """调用 PM：研究和回应都用它。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: CaseContext
    snapshot: Snapshot  # 本案件现在的快照
    turns: tuple[PmTurn, ...] = ()  # PM 在本案件的对话，按先后


class CriticRequest(BaseModel):
    """调用 Critic。要审核的方案是 conclusion 的方案；PM 修改过的条款在对话的回应里。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: CaseContext
    snapshot: Snapshot
    conclusion: HoldingConclusion | WatchConclusion  # 带交易方案的结论
    research: tuple[TaskOutcome, ...]  # PM 在本案件派过的任务和 Result，按派发的先后
    turns: tuple[CriticTurn, ...] = ()  # Critic 在本案件的对话，按先后


class SubAgentRequest(BaseModel):
    """调用子 agent：它只看到 Task 和快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: Task
    snapshot: Snapshot


class ReviewerRequest(BaseModel):
    """调用 Reviewer。复盘要读的交易记录和案件记录由 M6 加。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: Snapshot


class RetrospectiveReport(BaseModel):
    """复盘报告。内容由 M6 定，现在只有一段总结。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1)
