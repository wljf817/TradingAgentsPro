"""队列的性质测试（add-case-queue 的 design D11）。

从基准的股票状态开始，随机交错入队、案件结束、离开调用模型的阶段、换并发上限、方案的产生和进入
下一个阶段、股票状态变化、紧急退出和结束、降级和两个预算标志。每次变化以后取得队列的决定并执行，
对照 case-queue spec 检查这次决定，再检查执行以后的不变量。参考检查只用 spec 的规则和 define_case，
不读队列的实现。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from tests.modules.scheduler.builders import STATUSES, at
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    EventTrigger,
    FollowUpTrigger,
    HeartbeatTrigger,
    PendingPlan,
    PlanStage,
    QueuedCase,
    QueueDecision,
    QueueSnapshot,
    RunningCase,
    SchedulerConfig,
    SellTrigger,
    Trigger,
    define_case,
    emergency_decision,
    queue_decision,
)

SYMBOLS = tuple(STATUSES)
# 触发时刻只取几个值，常常相同，碰入队先后的比较
TIMES = (at(13), at(14), at(20))
RETROSPECTIVES = frozenset({CaseType.WEEKLY_RETROSPECTIVE, CaseType.SELL_RETROSPECTIVE})
# spec「调用预算」的表：普通预算用完后照常启动的五种持仓案件
HOLDING_CASES = frozenset(
    {
        CaseType.HOLDING_RISK_EVENT,
        CaseType.HOLDING_EVENT,
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.HOLDING_FOLLOW_UP,
    }
)
CONFIRMING = frozenset({PlanStage.AWAITING_CONFIRMATION, PlanStage.AWAITING_ORDER})
NEXT_STAGE = {
    PlanStage.REVIEW: PlanStage.AWAITING_CONFIRMATION,
    PlanStage.AWAITING_CONFIRMATION: PlanStage.AWAITING_ORDER,
}
# 定时心跳里针对一个状态、带股票的检查项开出的案件类型
STATUS_HEARTBEATS = (
    CaseType.HOLDING_REVIEW,
    CaseType.HOLDING_CATALYST_REVIEW,
    CaseType.WATCH_REVIEW,
    CaseType.WATCH_CATALYST_REVIEW,
    CaseType.CANDIDATE_EVALUATION,
)

type Flag = Literal["degraded", "general_budget_exhausted", "exploration_budget_exhausted"]


_times = st.sampled_from(TIMES)


def _blocked_by_budget(case_type: CaseType, snapshot: QueueSnapshot) -> bool:
    """spec「调用预算」的表：这种案件在这份快照下留在队列里。"""
    if case_type is CaseType.EXPLORATION:
        return snapshot.exploration_budget_exhausted
    return snapshot.general_budget_exhausted and case_type not in HOLDING_CASES


def _holds_stock(case_type: CaseType) -> bool:
    """复盘以外的案件占着它的股票；寻股的股票是 None，所以寻股一次一个。"""
    return case_type not in RETROSPECTIVES


class QueueMachine(RuleBasedStateMachine):
    """调用方的一份模型：排队的案件、运行中的案件、方案、股票状态和几个标志，每次变化以后执行决定。"""

    def __init__(self) -> None:
        super().__init__()
        self.config = SchedulerConfig()
        self.queue: list[QueuedCase] = []
        self.running: list[RunningCase] = []
        self.plans: list[PendingPlan] = []
        self.statuses: dict[str, StockStatus] = dict(STATUSES)
        self.exiting: set[str] = set()
        self.flags: dict[Flag, bool] = {
            "degraded": False,
            "general_budget_exhausted": False,
            "exploration_budget_exhausted": False,
        }
        self.ids = 0

    def _new_id(self) -> UUID:
        self.ids += 1
        return UUID(int=self.ids)

    def _snapshot(self) -> QueueSnapshot:
        return QueueSnapshot(
            queued=tuple(self.queue),
            running=tuple(self.running),
            plans=tuple(self.plans),
            statuses=dict(self.statuses),
            exiting=frozenset(self.exiting),
            degraded=self.flags["degraded"],
            general_budget_exhausted=self.flags["general_budget_exhausted"],
            exploration_budget_exhausted=self.flags["exploration_budget_exhausted"],
        )

    def _define(self, trigger: Trigger) -> CaseDefinition | None:
        symbol = trigger.symbol
        return define_case(trigger, None if symbol is None else self.statuses.get(symbol))

    def _calling_cases(self) -> list[RunningCase]:
        """还在调用模型、复盘以外的运行中案件。"""
        return [r for r in self.running if r.calling_models and _holds_stock(r.case_type)]

    def _held_symbols(self) -> list[str]:
        """有针对它的、复盘以外的案件在运行的股票。"""
        return [
            r.symbol for r in self.running if r.symbol is not None and _holds_stock(r.case_type)
        ]

    def _holdings_not_exiting(self) -> list[str]:
        return [
            s
            for s in SYMBOLS
            if self.statuses.get(s) is StockStatus.HOLDING and s not in self.exiting
        ]

    def _active_symbols(self) -> list[str]:
        """有案件在运行、有方案或正在紧急退出的股票。"""
        active = {r.symbol for r in self.running if r.symbol is not None}
        return sorted(active | {p.symbol for p in self.plans} | self.exiting)

    # 取得并执行队列的决定

    def _settle(self) -> None:
        snapshot = self._snapshot()
        decision = queue_decision(snapshot, self.config)
        self._check(snapshot, decision)
        voided, dropped = set(decision.void), set(decision.drop)
        started = {s.case_id for s in decision.start}
        self.plans = [p for p in self.plans if p.plan_id not in voided]
        self.queue = [q for q in self.queue if q.case_id not in dropped | started]
        self.running += [
            RunningCase(s.case_id, s.definition.case_type, s.definition.symbol, True)
            for s in decision.start
        ]

    def _check(self, snapshot: QueueSnapshot, decision: QueueDecision) -> None:
        """对照 spec 检查一次队列的决定。"""
        definitions = {q.case_id: self._define(q.trigger) for q in snapshot.queued}
        position = {q.case_id: i for i, q in enumerate(snapshot.queued)}
        triggered_at = {q.case_id: q.triggered_at for q in snapshot.queued}
        waiting = {case_id: d for case_id, d in definitions.items() if d is not None}

        def order(case_id: UUID) -> tuple[int, datetime, int]:
            return (waiting[case_id].priority, triggered_at[case_id], position[case_id])

        # 不再运行的就是这次确定不了类型的
        assert sorted(decision.drop) == sorted(c for c, d in definitions.items() if d is None)
        if decision.drop:
            event("确定不了类型")

        # 作废：这只股票有复盘以外、确定得了类型的案件在排队，已经交给人工确认的开仓类方案
        queued_symbols = {d.symbol for d in waiting.values() if _holds_stock(d.case_type)}
        assert sorted(decision.void) == sorted(
            p.plan_id
            for p in snapshot.plans
            if p.action.is_opening and p.stage in CONFIRMING and p.symbol in queued_symbols
        )
        if decision.void:
            event("作废开仓方案")

        # 启动：用这次确定的类型，按顺序；降级、预算、占着的股票和紧急退出都挡住
        started = [s.case_id for s in decision.start]
        assert len(set(started)) == len(started)
        assert all(waiting[s.case_id] == s.definition for s in decision.start)
        assert started == sorted(started, key=order)
        if snapshot.degraded:
            assert started == []
            if waiting:
                event("降级挡住")
            return
        held = {r.symbol for r in snapshot.running if _holds_stock(r.case_type)}
        started_holding: dict[str | None, UUID] = {}
        for start in decision.start:
            d = start.definition
            assert not _blocked_by_budget(d.case_type, snapshot)
            if _holds_stock(d.case_type):
                assert d.symbol not in snapshot.exiting
                assert d.symbol not in held
                assert d.symbol not in started_holding
                started_holding[d.symbol] = start.case_id
        calling = sum(r.calling_models for r in snapshot.running)
        if started:
            assert calling + len(started) <= self.config.max_concurrent_cases

        # 留在队列里的：要么被挡住，要么名额用完了、排在每个启动的案件之后
        for case_id, d in waiting.items():
            if case_id in started:
                continue
            if _blocked_by_budget(d.case_type, snapshot):
                event("预算挡住")
                continue
            if _holds_stock(d.case_type):
                if d.symbol in snapshot.exiting:
                    event("紧急退出期间被跳过")
                    continue
                if d.symbol in held or d.symbol in started_holding:
                    event("寻股被跳过" if d.symbol is None else "股票忙被跳过")
                    if d.symbol not in held:
                        assert order(started_holding[d.symbol]) < order(case_id)
                    continue
            event("名额用完")
            assert calling + len(started) >= self.config.max_concurrent_cases
            assert all(order(s) < order(case_id) for s in started)

    # 变化

    @initialize(limit=st.integers(1, 3))
    def configure(self, limit: int) -> None:
        self.config = SchedulerConfig(max_concurrent_cases=limit)

    @rule(limit=st.integers(1, 3))
    def restart_with_another_limit(self, limit: int) -> None:
        """调整并发上限以后重启：调低时调用模型的案件可能超过上限，不中止。"""
        running = list(self.running)
        self.config = SchedulerConfig(max_concurrent_cases=limit)
        self._settle()
        assert self.running[: len(running)] == running

    # 入队按触发分成几条规则：Hypothesis 每个样例随机关掉一些规则，不会一下子关掉全部入队

    def _symbol(self, data: st.DataObject) -> str:
        """触发针对的股票：常常是有案件、有方案或正在紧急退出的股票。"""
        symbols = st.sampled_from(SYMBOLS)
        if active := self._active_symbols():
            symbols |= st.sampled_from(active)
        return data.draw(symbols)

    def _enqueue(self, trigger: Trigger, triggered_at: datetime) -> None:
        self.queue.append(QueuedCase(self._new_id(), trigger, triggered_at))
        self._settle()

    @rule(
        data=st.data(),
        category=st.sampled_from(EventCategory),
        holding_risk=st.booleans(),
        triggered_at=_times,
    )
    def enqueue_event(
        self,
        data: st.DataObject,
        category: EventCategory,
        holding_risk: bool,
        triggered_at: datetime,
    ) -> None:
        self._enqueue(EventTrigger(self._symbol(data), category, holding_risk), triggered_at)

    @rule(data=st.data(), case_type=st.sampled_from(STATUS_HEARTBEATS), triggered_at=_times)
    def enqueue_stock_heartbeat(
        self, data: st.DataObject, case_type: CaseType, triggered_at: datetime
    ) -> None:
        """定时心跳里针对一个状态的检查项。"""
        self._enqueue(HeartbeatTrigger(case_type, self._symbol(data)), triggered_at)

    @rule(
        case_type=st.sampled_from((CaseType.EXPLORATION, CaseType.WEEKLY_RETROSPECTIVE)),
        triggered_at=_times,
    )
    def enqueue_exploration_or_weekly(self, case_type: CaseType, triggered_at: datetime) -> None:
        """股票池候选不足或每周固定时间。"""
        self._enqueue(HeartbeatTrigger(case_type), triggered_at)

    @rule(data=st.data(), triggered_at=_times)
    def enqueue_follow_up(self, data: st.DataObject, triggered_at: datetime) -> None:
        self._enqueue(FollowUpTrigger(self._symbol(data), "申请时写下的问题"), triggered_at)

    @rule(data=st.data(), triggered_at=_times)
    def enqueue_sell_fill(self, data: st.DataObject, triggered_at: datetime) -> None:
        self._enqueue(SellTrigger(self._symbol(data)), triggered_at)

    @precondition(lambda self: self.running)
    @rule(data=st.data())
    def finish_case(self, data: st.DataObject) -> None:
        self.running.remove(data.draw(st.sampled_from(self.running)))
        self._settle()

    @precondition(lambda self: self._calling_cases())
    @rule(data=st.data())
    def leave_calling_models(self, data: st.DataObject) -> None:
        """Critic 审核结束以后，案件进入风控、人工确认和下单，不再调用模型；复盘案件一直调用。"""
        case = data.draw(st.sampled_from(self._calling_cases()))
        index = self.running.index(case)
        self.running[index] = RunningCase(case.case_id, case.case_type, case.symbol, False)
        self._settle()

    @precondition(lambda self: self._held_symbols())
    @rule(data=st.data(), action=st.sampled_from(TradeAction))
    def propose_plan(self, data: st.DataObject, action: TradeAction) -> None:
        """运行中的、针对单只股票、复盘以外的案件给出这只股票的方案，先进入审核。"""
        symbol = data.draw(st.sampled_from(self._held_symbols()))
        self.plans.append(PendingPlan(self._new_id(), symbol, action, PlanStage.REVIEW))
        self._settle()

    @precondition(lambda self: self.plans)
    @rule(data=st.data())
    def advance_plan(self, data: st.DataObject) -> None:
        """方案进入下一个阶段；等下单的方案提交订单以后离开快照。"""
        plan = data.draw(st.sampled_from(self.plans))
        index = self.plans.index(plan)
        if plan.stage is PlanStage.AWAITING_ORDER:
            del self.plans[index]
        else:
            self.plans[index] = PendingPlan(
                plan.plan_id, plan.symbol, plan.action, NEXT_STAGE[plan.stage]
            )
        self._settle()

    @rule(symbol=st.sampled_from(SYMBOLS), status=st.sampled_from((*StockStatus, None)))
    def change_status(self, symbol: str, status: StockStatus | None) -> None:
        if status is None:
            self.statuses.pop(symbol, None)
        else:
            self.statuses[symbol] = status
        self._settle()

    @precondition(lambda self: self._holdings_not_exiting())
    @rule(data=st.data())
    def emergency_exit(self, data: st.DataObject) -> None:
        """持仓触发紧急风险退出：执行紧急退出的决定，再记为正在紧急退出。"""
        symbol = data.draw(st.sampled_from(self._holdings_not_exiting()))
        queue = list(self.queue)
        others_running = [r for r in self.running if r.symbol != symbol]
        others_plans = [p for p in self.plans if p.symbol != symbol]

        decision = emergency_decision(self._snapshot(), symbol)

        if decision.abort is not None:
            event("紧急退出中止案件")
        self.running = [r for r in self.running if r.case_id != decision.abort]
        voided = set(decision.void)
        self.plans = [p for p in self.plans if p.plan_id not in voided]
        assert not any(r.symbol == symbol and _holds_stock(r.case_type) for r in self.running)
        assert not any(p.symbol == symbol for p in self.plans)
        assert self.queue == queue
        assert [r for r in self.running if r.symbol != symbol] == others_running
        assert self.plans == others_plans
        self.exiting.add(symbol)
        self._settle()

    @precondition(lambda self: self.exiting)
    @rule(data=st.data(), filled=st.booleans())
    def emergency_exit_over(self, data: st.DataObject, filled: bool) -> None:
        """清仓卖单结束：成交以后代码清仓的持仓转为候选；被拒或撤销时还是持仓。"""
        symbol = data.draw(st.sampled_from(sorted(self.exiting)))
        self.exiting.remove(symbol)
        if filled:
            self.statuses[symbol] = StockStatus.CANDIDATE
        self._settle()

    @rule(
        flag=st.sampled_from(
            ("degraded", "general_budget_exhausted", "exploration_budget_exhausted")
        )
    )
    def toggle(self, flag: Flag) -> None:
        """进入或退出降级模式，普通预算或寻股预算用完或恢复。"""
        self.flags[flag] = not self.flags[flag]
        self._settle()

    # 执行决定以后的不变量

    @invariant()
    def one_case_per_stock_and_one_exploration(self) -> None:
        """每只股票最多一个复盘以外的案件在运行；寻股的股票是 None，所以最多一个寻股。"""
        held = [r.symbol for r in self.running if _holds_stock(r.case_type)]
        assert len(held) == len(set(held))

    @invariant()
    def nothing_runs_on_an_exiting_stock(self) -> None:
        assert not any(r.symbol in self.exiting for r in self.running if _holds_stock(r.case_type))

    @invariant()
    def queued_cases_all_have_a_type(self) -> None:
        assert all(self._define(q.trigger) is not None for q in self.queue)

    @invariant()
    def voiding_holds_at_all_times(self) -> None:
        """没有一个已经交给人工确认的开仓类方案，它的股票有复盘以外的案件在排队。"""
        queued_symbols = set()
        for q in self.queue:
            d = self._define(q.trigger)
            if d is not None and _holds_stock(d.case_type):
                queued_symbols.add(d.symbol)
        assert not any(
            p.action.is_opening and p.stage in CONFIRMING and p.symbol in queued_symbols
            for p in self.plans
        )


TestQueueMachine = QueueMachine.TestCase
