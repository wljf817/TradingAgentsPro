"""Task 和 Result：PM 和 Critic 经 Agent 网关派给子 agent 的任务，和子 agent 交回的结果。

任务请求是 PM 和 Critic 的 dispatch_tasks 工具参数，来自模型输出，所以在这里校验格式；Task 和
Result 要写进状态库，案件记录、工具结果和评估都要读，所以放在 contracts。放行还是拒绝、Result 怎样
生成由 Agent 网关决定。见 task-dispatch spec「任务清单」「任务请求的格式」「Task 的格式」「Result
的格式」；Result 记下的实际使用的模型见 model-fallback spec「实际使用的模型」。
"""

from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.signal import EntryId, NoSignal, Signal


class TaskType(StrEnum):
    """任务类型，见 task-dispatch spec「任务清单」。每种只属于一个子 agent。"""

    EVENT_IMPACT = "event_impact"  # 事件影响评估
    NEWS_SINCE = "news_since"  # 某日期以来的新闻汇总：起始日期写在问题里
    PRICE_REACTION = "price_reaction"  # 价格反应
    TREND_LEVELS = "trend_levels"  # 趋势和支撑阻力
    VOLATILITY = "volatility"  # 波动情况
    EARNINGS_VS_EXPECTATIONS = "earnings_vs_expectations"  # 财报对比预期
    GUIDANCE_CHANGE = "guidance_change"  # 指引变化
    VALUATION = "valuation"  # 估值位置

    @property
    def agent(self) -> SubAgent:
        """这种任务的目标：所属的子 agent。"""
        return _AGENTS[self]


_AGENTS: Final = {
    TaskType.EVENT_IMPACT: SubAgent.NEWS,
    TaskType.NEWS_SINCE: SubAgent.NEWS,
    TaskType.PRICE_REACTION: SubAgent.QUANT,
    TaskType.TREND_LEVELS: SubAgent.QUANT,
    TaskType.VOLATILITY: SubAgent.QUANT,
    TaskType.EARNINGS_VS_EXPECTATIONS: SubAgent.FUNDAMENTALS,
    TaskType.GUIDANCE_CHANGE: SubAgent.FUNDAMENTALS,
    TaskType.VALUATION: SubAgent.FUNDAMENTALS,
}


class Dispatcher(StrEnum):
    """任务的发起者：只有 PM 和 Critic 能派任务。由调用方按调用工具的 agent 填写，不是模型写的。"""

    PM = "pm"
    CRITIC = "critic"


class TaskRequest(BaseModel):
    """发起者写的一个任务请求：dispatch_tasks 工具参数里的一项。不写目标和发起者。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    task_type: TaskType
    symbol: str = Field(min_length=1)  # 股票代码
    question: str = Field(min_length=1)  # 在任务类型之外补充的具体关注点
    inputs: tuple[EntryId, ...] = ()  # 状态库条目引用，可以为空


class Task(BaseModel):
    """网关生成的任务：请求的原样内容，加上网关填写的几项。生成以后不能修改。

    派发出去的和被拒绝的请求都生成 Task；被拒绝的 PM 任务没有轮次，由网关保证。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    task_id: UUID
    case_id: UUID
    dispatcher: Dispatcher
    task_type: TaskType
    symbol: str = Field(min_length=1)
    question: str = Field(min_length=1)
    inputs: tuple[EntryId, ...] = ()
    dispatched_at: AwareDatetime  # 派发时刻
    deadline: AwareDatetime  # 截止时间：派发时刻加任务时限
    pm_round: int | None = Field(default=None, ge=1)  # PM 派发出去的任务是第几轮

    @property
    def target(self) -> SubAgent:
        """目标 agent：任务类型所属的子 agent。"""
        return self.task_type.agent

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.deadline <= self.dispatched_at:
            raise ValueError("截止时间必须晚于派发时刻")
        if self.dispatcher is Dispatcher.CRITIC and self.pm_round is not None:
            raise ValueError("Critic 的任务没有 PM 的轮次")
        return self


class ResultStatus(StrEnum):
    """Result 的状态。"""

    COMPLETED = "completed"  # 完成：输出是信号
    NO_SIGNAL = "no_signal"  # 无信号：输出是无信号
    TIMED_OUT = "timed_out"  # 超时
    FAILED = "failed"  # 失败：格式不合法或调用失败
    REJECTED = "rejected"  # 被拒绝：网关没有派发


class TaskRejection(StrEnum):
    """被拒绝的原因，按 spec「派发的顺序」。"""

    NOT_CASE_STOCK = "not_case_stock"  # 股票不是案件的股票
    EXPLORATION_TARGET = "exploration_target"  # 寻股只能派给 Quant 和 Fundamentals
    ROUNDS_USED_UP = "rounds_used_up"  # 轮次已用完
    PM_TASKS_USED_UP = "pm_tasks_used_up"  # 任务数已用完
    CRITIC_TASKS_USED_UP = "critic_tasks_used_up"  # 补充研究额度已用完


class TaskFailure(StrEnum):
    """失败的原因。"""

    FORMAT_INVALID = "format_invalid"  # 格式不合法：不重新调用，不切换模型
    CALL_FAILED = "call_failed"  # 调用失败：model-fallback spec 的全部不可用


# 有输出的状态和输出的类型；带原因的状态和原因的类型
_OUTPUTS: Final = {ResultStatus.COMPLETED: Signal, ResultStatus.NO_SIGNAL: NoSignal}
_REASONS: Final = {ResultStatus.FAILED: TaskFailure, ResultStatus.REJECTED: TaskRejection}


class Result(BaseModel):
    """一个 Task 的 Result，用 task_id 标识。各状态带什么见 spec「Result 的格式」。

    调度器的结论校验要的 Result ID 是 task_id 的文字。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    task_id: UUID
    status: ResultStatus
    output: Signal | NoSignal | None = None
    reason: TaskFailure | TaskRejection | None = None
    detail: str | None = Field(default=None, min_length=1)  # 失败的说明
    elapsed: timedelta = Field(ge=timedelta(0))  # 耗时：生成 Result 的时刻减派发时刻
    # 这个任务各次尝试记下的费用之和，见 call-budget spec「子 agent 任务的费用」
    cost: Decimal = Field(ge=0)
    model_use: ModelUse | None = None  # 给出输出的模型：完成、无信号、格式不合法的失败才有

    @property
    def cited_entries(self) -> tuple[EntryId, ...]:
        """引用的条目：取自输出；没有输出时为空。"""
        return () if self.output is None else self.output.cited_entries

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        if not _matches(self.output, _OUTPUTS.get(self.status)):
            raise ValueError("只有完成和无信号有输出：完成是信号，无信号是无信号")
        if not _matches(self.reason, _REASONS.get(self.status)):
            raise ValueError("失败带失败的原因，被拒绝带被拒绝的原因，其他状态不带")
        if (self.status is ResultStatus.FAILED) != (self.detail is not None):
            raise ValueError("失败必须写说明，其他状态不写")
        received = self.status in _OUTPUTS or self.reason is TaskFailure.FORMAT_INVALID
        if received != (self.model_use is not None):
            raise ValueError("完成、无信号和格式不合法的失败记下给出输出的模型，其他状态不记")
        return self


def _matches(value: object, expected: type | None) -> bool:
    """这个状态不带这一项（expected 为 None）时 value 为空，带时 value 是 expected 的实例。"""
    return value is None if expected is None else isinstance(value, expected)
