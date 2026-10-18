"""案件工作流每一步的用例：开始运行、生成快照、组出案件上下文、调用 PM、Critic 和 Reviewer、派发和
运行任务、检查结论并让它生效、写审核、回应和风控结果、写案件阶段变化、结束。

每一步都是普通的方法，不 import dbos；DBOS 工作流在 case_workflow.py 里，只按这里的结果分支。见
case-workflow spec 和 add-case-workflow 的 design D1。每次调用 agent 一步，结果就是 agent 的回复；
写入放在后面的步骤里。程序日志的调度器动作（开案件、案件阶段变化、唤醒 agent）在这里写。
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.agent_call import (
    CaseContext,
    CriticReply,
    CriticRequest,
    CriticTurn,
    PmReply,
    PmRequest,
    PmTurn,
    RecentCase,
    Returned,
    ReviewerRequest,
    TaskOutcome,
)
from tradingagentspro.contracts.case import CaseEndReason
from tradingagentspro.contracts.conclusion import (
    Conclusion,
    FollowUpRequest,
    HoldingConclusion,
    WatchConclusion,
)
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.review import CriticReview, PlanResponse
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.snapshot import Snapshot
from tradingagentspro.contracts.task import Dispatcher, TaskRequest
from tradingagentspro.contracts.thesis import Catalyst, InvestmentThesis, WatchThesis
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.caserecords import CaseRecord, CaseRecordService
from tradingagentspro.modules.execution import BrokerUnavailableError
from tradingagentspro.modules.followups import FollowUpRejection, FollowUpService
from tradingagentspro.modules.gateway import (
    AgentCallService,
    AllUnavailable,
    Answered,
    CallBudgetService,
    CaseScope,
    Dispatch,
    ModelFallbackService,
    TaskDispatchService,
    TaskRecord,
    usage,
)
from tradingagentspro.modules.marketdata import QuoteUnavailableError
from tradingagentspro.modules.notification import NotificationService
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    CaseState,
    EventTrigger,
    SchedulerConfig,
    Trigger,
)
from tradingagentspro.modules.screener import ScreenDataUnavailableError
from tradingagentspro.modules.statestore import SnapshotService
from tradingagentspro.modules.stockpool import PoolEntry, StockPoolService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows.case_conclusion import (
    CheckConfigs,
    ConclusionEffects,
    conclusion_problem,
)
from tradingagentspro.workflows.case_entries import (
    RETROSPECTIVE,
    Reply,
    case_trigger,
    open_case_text,
    rejection_text,
    stage_text,
    unavailable_alert,
    wake_failed_text,
    wake_text,
)
from tradingagentspro.workflows.snapshot_flow import SnapshotFlow

_log = logging.getLogger(__name__)

# 生成快照时这几种读不到按系统故障结束：券商、行情、粗筛的数据源断线（state-snapshot spec
# 「数据读不到时」把处理交给调用方，见 design D1）；数据库出错原样抛出
_UNREADABLE = (BrokerUnavailableError, QuoteUnavailableError, ScreenDataUnavailableError)


class CaseFlowConfig(BaseModel):
    """案件工作流的配置，含义见 case-workflow spec「案件工作流的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recent_cases: int = Field(default=3, ge=0)  # 案件上下文里最近结束的案件最多几个


@dataclass(frozen=True, slots=True)
class CaseServices:
    """案件工作流用到的用例、时钟、交易日历和 ID 生成。"""

    agents: AgentCallService
    tasks: TaskDispatchService
    budget: CallBudgetService
    fallback: ModelFallbackService
    records: CaseRecordService
    snapshots: SnapshotService
    snapshot_flow: SnapshotFlow
    pool: StockPoolService
    follow_ups: FollowUpService
    notification: NotificationService
    log: ProgramLog
    clock: Clock
    calendar: TradingCalendar
    ids: IdGenerator


@dataclass(frozen=True, slots=True)
class Accepted:
    """结论通过检查并已经生效：现在的方案；没有交易方案时为 None。"""

    plan: TradePlan | None


type Concluded = Returned | Accepted


class CaseFlow:
    """案件工作流每一步的用例，见 case-workflow spec。每个方法对应工作流的一个 step。"""

    def __init__(
        self, services: CaseServices, configs: CheckConfigs, config: CaseFlowConfig
    ) -> None:
        self._s = services
        self._configs = configs
        self._config = config
        self._effects = ConclusionEffects(services.pool, services.follow_ups, services.records)

    @property
    def scheduler_config(self) -> SchedulerConfig:
        """推进案件状态用的调度器配置：结论最多重交次数、质询最多轮数。"""
        return self._configs.scheduler

    def start(
        self, case_id: UUID, definition: CaseDefinition, trigger: Trigger, triggered_at: datetime
    ) -> None:
        """写案件记录的开始运行和程序日志的开案件。"""
        recorded = case_trigger(trigger, triggered_at)
        self._s.records.start(case_id, recorded, definition.case_type)
        self._write(Action.OPEN_CASE, case_id, open_case_text(definition, recorded))

    def snapshot(
        self, case_id: UUID, definition: CaseDefinition, version: int, reason: str | None
    ) -> int | None:
        """生成第 version 版快照，给出版本号；券商、行情、粗筛的数据源读不到时为 None。"""
        try:
            self._s.snapshot_flow.take(
                case_id, definition.case_type, definition.symbol, version=version, reason=reason
            )
        except _UNREADABLE as error:
            _log.warning("案件 %s 的第 %d 版快照读不到数据：%s", case_id, version, error)
            return None
        return version

    def context(self, definition: CaseDefinition, trigger: Trigger) -> CaseContext:
        """组出案件上下文，见 spec「案件上下文」。"""
        assert definition.question is not None  # 由 PM 处理的案件都有问题
        symbol = definition.symbol
        entry = None if symbol is None else self._s.pool.entry(symbol)
        recent: Sequence[CaseRecord] = ()
        pending: tuple[FollowUpRequest, ...] = ()
        if symbol is not None:
            recent = self._s.records.recent_ended(symbol, self._config.recent_cases)
            pending = tuple(f.request for f in self._s.follow_ups.pending(symbol))
        return CaseContext(
            question=definition.question,
            event=trigger.category if isinstance(trigger, EventTrigger) else None,
            required_agent=definition.required_agent,
            watch_thesis=None if entry is None else entry.watch_thesis,
            investment_thesis=None if entry is None else entry.investment_thesis,
            recent_cases=tuple(_recent(r) for r in recent),
            pending_follow_ups=pending,
        )

    # 调用 agent：每次调用交回以后写唤醒 agent，全部不可用时告警、给出 None

    def call_pm(
        self,
        case_id: UUID,
        version: int,
        context: CaseContext,
        turns: Sequence[PmTurn],
        why: str,
    ) -> Answered[PmReply] | None:
        """调用 PM 做研究：派发任务或提交结论。"""
        snapshot = self._snapshot(case_id, version)
        request = PmRequest(context=context, snapshot=snapshot, turns=tuple(turns))
        return self._called(Agent.PM, why, snapshot, lambda: self._s.agents.pm(request))

    def call_pm_respond(
        self,
        case_id: UUID,
        version: int,
        context: CaseContext,
        turns: Sequence[PmTurn],
        why: str,
    ) -> Answered[PlanResponse] | None:
        """调用 PM 回应对话最后一轮交回的质询。"""
        snapshot = self._snapshot(case_id, version)
        request = PmRequest(context=context, snapshot=snapshot, turns=tuple(turns))
        return self._called(Agent.PM, why, snapshot, lambda: self._s.agents.pm_respond(request))

    def call_critic(
        self,
        case_id: UUID,
        version: int,
        context: CaseContext,
        conclusion: HoldingConclusion | WatchConclusion,
        turns: Sequence[CriticTurn],
        why: str,
    ) -> Answered[CriticReply] | None:
        """调用 Critic：补充研究或审核；带上 PM 在本案件派过的任务和 Result，按派发的先后。"""
        snapshot = self._snapshot(case_id, version)
        research = tuple(
            _outcome(r)
            for r in self._s.tasks.records(case_id)
            if r.task.dispatcher is Dispatcher.PM
        )
        request = CriticRequest(
            context=context,
            snapshot=snapshot,
            conclusion=conclusion,
            research=research,
            turns=tuple(turns),
        )
        return self._called(Agent.CRITIC, why, snapshot, lambda: self._s.agents.critic(request))

    def call_reviewer(self, case_id: UUID, version: int) -> bool:
        """调用 Reviewer 复盘；报告的总结写进唤醒 agent 的条目。调用失败时为假。"""
        snapshot = self._snapshot(case_id, version)
        request = ReviewerRequest(snapshot=snapshot)
        called = self._called(
            Agent.REVIEWER, RETROSPECTIVE, snapshot, lambda: self._s.agents.reviewer(request)
        )
        return called is not None

    def _called[T: Reply](
        self,
        agent: Agent,
        why: str,
        snapshot: Snapshot,
        call: Callable[[], Answered[T] | AllUnavailable],
    ) -> Answered[T] | None:
        started = self._s.clock.now()
        outcome = call()
        duration = self._s.clock.now() - started
        case_id = snapshot.case_id
        if isinstance(outcome, AllUnavailable):
            text = wake_failed_text(agent, why, outcome.detail)
            self._write(Action.WAKE_AGENT, case_id, text, duration=duration)
            alert = unavailable_alert(
                case_id, snapshot.case_type, snapshot.symbol, agent, outcome.detail
            )
            self._s.notification.alert(str(case_id), alert)
            return None
        text = wake_text(agent, why, outcome.output, outcome.model_use)
        self._write(Action.WAKE_AGENT, case_id, text, duration=duration, cost=outcome.cost)
        return outcome

    # 任务

    def dispatch(
        self,
        case_id: UUID,
        definition: CaseDefinition,
        dispatcher: Dispatcher,
        requests: Sequence[TaskRequest],
    ) -> tuple[Dispatch, ...]:
        """经网关派发一次：每个请求的处理结果，派发出去的由工作流交给子 agent 运行。"""
        scope = CaseScope(case_id, definition.case_type, definition.symbol)
        return self._s.tasks.dispatch(scope, dispatcher, requests)

    def run_task(self, task_id: UUID, case_id: UUID, version: int) -> None:
        """运行一个派发出去的任务，读第 version 版快照；已经有 Result 时不再调用模型。"""
        self._s.agents.run_task(task_id, self._snapshot(case_id, version))

    def outcomes(self, task_ids: Sequence[UUID]) -> tuple[TaskOutcome, ...]:
        """每个请求的 Task 和 Result，按请求的顺序；派发出去的任务都已经有 Result。"""
        return tuple(_outcome(self._s.tasks.record(t)) for t in task_ids)

    # 结论

    def conclude(
        self,
        case_id: UUID,
        definition: CaseDefinition,
        version: int,
        conclusion: Conclusion,
        model_use: ModelUse,
    ) -> Concluded:
        """按 spec「结论的检查」检查；通过时写案件记录的结论，让结论生效，给出现在的方案。"""
        s = self._s
        now = s.clock.now()
        snapshot = self._snapshot(case_id, version)
        symbol = definition.symbol
        entry = None if symbol is None else s.pool.entry(symbol)
        records = s.tasks.records(case_id)
        problem = conclusion_problem(
            definition,
            conclusion,
            model_use,
            records,
            entry,
            snapshot,
            now,
            s.calendar,
            self._configs,
        )
        if problem is None:
            problem = self._approve_follow_up(case_id, symbol, conclusion, entry)
        if problem is not None:
            return Returned(reason=rejection_text(problem))
        plan = self._plan(case_id, conclusion, symbol, entry)
        s.records.conclude(case_id, conclusion, model_use, plan)
        today = s.calendar.session_closing_after(now).day
        self._effects.apply(case_id, definition, conclusion, entry, snapshot, today)
        return Accepted(plan)

    def _approve_follow_up(
        self, case_id: UUID, symbol: str | None, conclusion: Conclusion, entry: PoolEntry | None
    ) -> FollowUpRejection | None:
        """第 6 项跟进审批：获批即保存；催化剂是结论生效以后这只股票论点里的。"""
        if not isinstance(conclusion, HoldingConclusion | WatchConclusion):
            return None
        request = conclusion.follow_up
        if request is None:
            return None
        assert symbol is not None and entry is not None
        approved = self._s.follow_ups.request(
            symbol, request, case_id, _catalyst(conclusion, entry)
        )
        return approved if isinstance(approved, FollowUpRejection) else None

    def _plan(
        self, case_id: UUID, conclusion: Conclusion, symbol: str | None, entry: PoolEntry | None
    ) -> TradePlan | None:
        """结论的交易方案：补上方案 ID、股票和板块；案件记录已经有方案时用它（重做的步骤）。"""
        if not isinstance(conclusion, HoldingConclusion | WatchConclusion):
            return None
        existing = self._s.records.proposal(case_id)
        if existing is not None:
            return existing.plan
        assert symbol is not None and entry is not None
        return conclusion.trade_plan(self._s.ids.new_id(), symbol, entry.sector)

    # 审核、回应和风控结果

    def record_review(self, case_id: UUID, review: CriticReview, model_use: ModelUse) -> None:
        self._s.records.review(case_id, review, model_use)

    def record_response(self, case_id: UUID, response: PlanResponse, model_use: ModelUse) -> None:
        self._s.records.respond(case_id, response, model_use)

    def critic_unavailable(self, case_id: UUID) -> None:
        self._s.records.critic_unavailable(case_id)

    def record_risk(self, case_id: UUID, decision: RiskDecision) -> None:
        """交接给出的风控结果写进案件记录。"""
        self._s.records.risk_checked(case_id, decision)

    # 阶段和结束

    def stage(
        self, case_id: UUID, before: CaseState, after: CaseState, plan: TradePlan | None
    ) -> None:
        """写一条案件阶段变化。"""
        self._write(Action.CASE_STAGE, case_id, stage_text(before, after, plan))

    def end(self, case_id: UUID, reason: CaseEndReason) -> None:
        """写案件记录的结束（Task 数和调用次数），清掉本案件的备用模型起点。"""
        used = usage(self._s.tasks.records(case_id))
        calls = len(self._s.budget.case_costs(case_id))
        self._s.records.end(case_id, reason, used.pm_tasks + used.critic_tasks, calls)
        self._s.fallback.end_case(case_id)

    def _snapshot(self, case_id: UUID, version: int) -> Snapshot:
        snapshot = self._s.snapshots.snapshot(case_id, version)
        assert snapshot is not None  # 版本号来自生成快照的那一步
        return snapshot

    def _write(
        self,
        action: Action,
        case_id: UUID,
        detail: str,
        *,
        duration: timedelta | None = None,
        cost: Decimal | None = None,
    ) -> None:
        """调度器的一条程序日志：对象和案件 ID 都是案件 ID；只有唤醒 agent 写耗时和费用。"""
        self._s.log.write(
            Actor.SCHEDULER,
            action,
            str(case_id),
            detail,
            case_id=case_id,
            duration=duration,
            cost=cost,
        )


def _outcome(record: TaskRecord) -> TaskOutcome:
    assert record.result is not None  # 派发出去的任务都已经运行完
    return TaskOutcome(task=record.task, result=record.result)


def _recent(record: CaseRecord) -> RecentCase:
    """案件上下文里的一个最近结束的案件。"""
    assert record.case_type is not None and record.ended_at is not None
    assert record.end_reason is not None
    conclusion = None if record.conclusion is None else record.conclusion.conclusion
    return RecentCase(
        case_type=record.case_type,
        ended_at=record.ended_at,
        end_reason=record.end_reason,
        conclusion=conclusion,
    )


def _catalyst(conclusion: HoldingConclusion | WatchConclusion, entry: PoolEntry) -> Catalyst:
    """结论生效以后这只股票论点里的催化剂：附了论点更新时是论点更新里的，否则是现在的论点里的。"""
    thesis: InvestmentThesis | WatchThesis | None
    if isinstance(conclusion, HoldingConclusion):
        thesis = conclusion.thesis or entry.investment_thesis
    else:
        thesis = conclusion.watch_thesis or entry.watch_thesis
    assert thesis is not None  # 持仓和关注在股票池里都有论点
    return thesis.catalyst
