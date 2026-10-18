"""PM 替身的派发和结论，以及 PM 和子 agent 替身共用的信号，见 stand-ins spec 和 add-stand-ins 的
design D8、D9。

都是纯函数：输出只由快照、案件上下文、要引用的 Result 和配置决定，不读时钟、不生成 ID。"今天"是
快照的交易日（生成时刻归属的交易日），和 review-dates spec 的"今天"是同一个定义。
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.agent_call import CaseContext
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    AfterClose,
    CandidateConclusion,
    Conclusion,
    Decision,
    ExplorationConclusion,
    HoldingConclusion,
    PlanDraft,
    PoolAddition,
    WatchConclusion,
)
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.signal import Signal
from tradingagentspro.contracts.snapshot import QuoteEntry, Snapshot
from tradingagentspro.contracts.task import TaskRequest, TaskType
from tradingagentspro.contracts.thesis import (
    Catalyst,
    Comparison,
    Condition,
    ConditionKind,
    InvestmentThesis,
    WatchThesis,
)
from tradingagentspro.contracts.trade_plan import OrderType
from tradingagentspro.integrations.standin.config import StandInConfig
from tradingagentspro.shared.trading_calendar import TradingCalendar

# stand-ins spec「PM 替身派发任务」：每个子 agent 一个任务；按 SubAgent 的顺序派
_TASKS: Final = {
    SubAgent.NEWS: TaskType.EVENT_IMPACT,
    SubAgent.QUANT: TaskType.PRICE_REACTION,
    SubAgent.FUNDAMENTALS: TaskType.EARNINGS_VS_EXPECTATIONS,
}
_RESEARCH: Final = frozenset({SubAgent.QUANT, SubAgent.FUNDAMENTALS})
_CENT: Final = Decimal("0.01")
_SUB_DOLLAR_TICK: Final = Decimal("0.0001")

_QUESTION = "替身任务：按任务类型给出固定的信号"
_ANSWER = "替身按案件类型给出固定的结论"
_RATIONALE = "替身不做判断，结论按案件类型固定，用来跑通案件流程"


def stand_in_signal(snapshot: Snapshot, cited: tuple[str, ...], config: StandInConfig) -> Signal:
    """替身的信号：评估窗口、评分和置信度来自配置，数据截至快照的生成时刻，理由和主要风险固定。"""
    return Signal(
        horizon=config.signal_horizon,
        score=config.signal_score,
        confidence=config.signal_confidence,
        data_as_of=snapshot.taken_at,
        cited_entries=cited,
        rationale="替身给出的固定信号，不是判断",
        risks="替身不做判断，没有风险分析",
    )


def researched(snapshot: Snapshot, context: CaseContext) -> bool:
    """结论是开仓方案或升级：要引用至少两个不同子 agent 的 Result。"""
    match snapshot.case_type:
        case CaseType.CANDIDATE_EVALUATION:
            return True
        case CaseType.WATCH_EVENT:
            return context.event is EventCategory.ENTRY
        case _:
            return False


def research_requests(snapshot: Snapshot, context: CaseContext) -> tuple[TaskRequest, ...]:
    """要派的任务：开仓方案和升级派 Quant 和 Fundamentals，事件案件另加事件要求的子 agent。"""
    agents = set(_RESEARCH) if researched(snapshot, context) else set()
    if context.required_agent is not None:
        agents.add(context.required_agent)
    if not agents:
        return ()
    symbol = _symbol(snapshot)
    quote = _quote(snapshot, symbol)
    inputs = () if quote is None else (quote.entry_id,)
    return tuple(
        TaskRequest(task_type=_TASKS[agent], symbol=symbol, question=_QUESTION, inputs=inputs)
        for agent in SubAgent
        if agent in agents
    )


def conclusion(
    context: CaseContext,
    snapshot: Snapshot,
    results: tuple[str, ...],
    calendar: TradingCalendar,
    config: StandInConfig,
) -> Conclusion:
    """按案件类型给出 stand-ins spec「PM 替身的结论」表里的结论；results 是要引用的 Result ID。"""
    today = snapshot.market.trading_day
    match snapshot.case_type:
        case CaseType.HOLDING_RISK_EVENT | CaseType.HOLDING_EVENT | CaseType.HOLDING_FOLLOW_UP:
            return _holding(snapshot, results, config, Decision.HOLD)
        case CaseType.HOLDING_REVIEW:
            renewal = _renewed(_current(context.investment_thesis), today, calendar, config)
            return _holding(snapshot, results, config, Decision.HOLD, thesis=renewal)
        case CaseType.HOLDING_CATALYST_REVIEW:
            close = PlanDraft(order_type=OrderType.MARKET)
            return _holding(
                snapshot, results, config, Decision.CLOSE, plan=close, after_close=AfterClose.REMOVE
            )
        case CaseType.WATCH_EVENT if context.event is EventCategory.ENTRY:
            return _buy(snapshot, results, calendar, config)
        case CaseType.WATCH_EVENT | CaseType.WATCH_FOLLOW_UP:
            return _watch(snapshot, results, config)
        case CaseType.WATCH_REVIEW | CaseType.WATCH_CATALYST_REVIEW:
            watch_renewal = _renewed(_current(context.watch_thesis), today, calendar, config)
            return _watch(snapshot, results, config, watch_thesis=watch_renewal)
        case CaseType.CANDIDATE_EVENT:
            return _candidate(snapshot, results, config, Decision.KEEP)
        case CaseType.CANDIDATE_EVALUATION:
            promotion = _promotion(snapshot, calendar, config)
            return _candidate(snapshot, results, config, Decision.PROMOTE, promotion)
        case CaseType.EXPLORATION:
            return _exploration(snapshot, results, config)
        case CaseType.WEEKLY_RETROSPECTIVE | CaseType.SELL_RETROSPECTIVE:
            raise ValueError(f"复盘案件 {snapshot.case_type} 不唤醒 PM，由 Reviewer 替身处理")


def to_tick(price: Decimal) -> Decimal:
    """按最小变动单位四舍五入：1 美元及以上到 0.01，低于 1 美元到 0.0001。"""
    tick = _CENT if price >= 1 else _SUB_DOLLAR_TICK
    return price.quantize(tick, rounding=ROUND_HALF_UP)


def buy_plan(snapshot: Snapshot, limit: Decimal, config: StandInConfig) -> PlanDraft:
    """开仓方案的条款，limit 是限价。股数按权益的比例，不足 1 股时买 1 股，交给风控判断。"""
    quantity = max(1, int(snapshot.account.equity * config.position_ratio / limit))
    return PlanDraft(
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=limit,
        stop_price=to_tick(limit * (1 - config.stop_ratio)),
        emergency_threshold=config.emergency_threshold,
    )


def new_catalyst(today: date, calendar: TradingCalendar, config: StandInConfig) -> Catalyst:
    """新的催化剂：描述带上截止日期，和原来的描述不同，续期时算新的催化剂。"""
    deadline = calendar.add_sessions(today, config.catalyst_sessions).day
    return Catalyst(description=f"替身催化剂，截止 {deadline.isoformat()}", deadline=deadline)


def _review_day(today: date, calendar: TradingCalendar, config: StandInConfig) -> date:
    return calendar.add_sessions(today, config.review_sessions).day


def _renewed[T: (WatchThesis, InvestmentThesis)](
    thesis: T, today: date, calendar: TradingCalendar, config: StandInConfig
) -> T:
    """续期的论点更新：照抄现在的论点，复查日改为新的；催化剂到期（不晚于今天）时换新的。"""
    catalyst = thesis.catalyst
    if catalyst.deadline <= today:
        catalyst = new_catalyst(today, calendar, config)
    review_day = _review_day(today, calendar, config)
    return thesis.model_copy(update={"review_day": review_day, "catalyst": catalyst})


def _current[T: (WatchThesis, InvestmentThesis)](thesis: T | None) -> T:
    """复查类案件的上下文里这只股票现在的论点。"""
    if thesis is None:
        raise ValueError("复查类案件的上下文没有这只股票现在的论点")
    return thesis


def _buy(
    snapshot: Snapshot, results: tuple[str, ...], calendar: TradingCalendar, config: StandInConfig
) -> WatchConclusion:
    """开仓方案：交易方案和新建仓的投资论点。"""
    today = snapshot.market.trading_day
    limit = to_tick(_reference_price(snapshot) * (1 + config.limit_markup))
    thesis = InvestmentThesis(
        rationale="替身：入场条件满足，按固定的规则开仓",
        catalyst=new_catalyst(today, calendar, config),
        target_price=to_tick(limit * (1 + config.target_ratio)),
        invalidation="替身：跌破止损价",
        review_day=_review_day(today, calendar, config),
    )
    return WatchConclusion(
        answer=_ANSWER,
        rationale=_RATIONALE,
        cited_results=results,
        signal=_case_signal(snapshot, config),
        decision=Decision.BUY,
        plan=buy_plan(snapshot, limit, config),
        thesis=thesis,
    )


def _promotion(snapshot: Snapshot, calendar: TradingCalendar, config: StandInConfig) -> WatchThesis:
    """升级附的关注论点：入场条件是价格不高于参考价乘（1 + 入场条件的比例）。"""
    today = snapshot.market.trading_day
    entry = Condition(
        kind=ConditionKind.PRICE,
        comparison=Comparison.AT_MOST,
        value=to_tick(_reference_price(snapshot) * (1 + config.entry_markup)),
    )
    return WatchThesis(
        rationale="替身：候选评估一律升级",
        entry_conditions=(entry,),
        catalyst=new_catalyst(today, calendar, config),
        invalidation="替身：入场条件长期不满足",
        review_day=_review_day(today, calendar, config),
    )


def _holding(
    snapshot: Snapshot,
    results: tuple[str, ...],
    config: StandInConfig,
    decision: Literal[Decision.HOLD, Decision.CLOSE],
    *,
    plan: PlanDraft | None = None,
    thesis: InvestmentThesis | None = None,
    after_close: AfterClose | None = None,
) -> HoldingConclusion:
    return HoldingConclusion(
        answer=_ANSWER,
        rationale=_RATIONALE,
        cited_results=results,
        signal=_case_signal(snapshot, config),
        decision=decision,
        plan=plan,
        thesis=thesis,
        after_close=after_close,
    )


def _watch(
    snapshot: Snapshot,
    results: tuple[str, ...],
    config: StandInConfig,
    *,
    watch_thesis: WatchThesis | None = None,
) -> WatchConclusion:
    """继续关注；复查类案件附续期的关注论点。"""
    return WatchConclusion(
        answer=_ANSWER,
        rationale=_RATIONALE,
        cited_results=results,
        signal=_case_signal(snapshot, config),
        decision=Decision.KEEP_WATCHING,
        watch_thesis=watch_thesis,
    )


def _candidate(
    snapshot: Snapshot,
    results: tuple[str, ...],
    config: StandInConfig,
    decision: Literal[Decision.KEEP, Decision.PROMOTE],
    watch_thesis: WatchThesis | None = None,
) -> CandidateConclusion:
    return CandidateConclusion(
        answer=_ANSWER,
        rationale=_RATIONALE,
        cited_results=results,
        signal=_case_signal(snapshot, config),
        decision=decision,
        watch_thesis=watch_thesis,
    )


def _exploration(
    snapshot: Snapshot, results: tuple[str, ...], config: StandInConfig
) -> ExplorationConclusion:
    """加入粗筛名单的前几只，不删除；每只候选的信号引用粗筛结果的条目。"""
    screen = snapshot.screen
    if screen is None:
        raise ValueError("寻股的快照没有粗筛结果")
    signal = stand_in_signal(snapshot, (screen.entry_id,), config)
    additions = tuple(
        PoolAddition(symbol=candidate.symbol, reason=f"替身：粗筛第 {rank} 名", signal=signal)
        for rank, candidate in enumerate(screen.candidates[: config.exploration_additions], 1)
    )
    return ExplorationConclusion(
        answer=_ANSWER, rationale=_RATIONALE, cited_results=results, additions=additions
    )


def _case_signal(snapshot: Snapshot, config: StandInConfig) -> Signal:
    """持仓、关注、候选的结论的信号：引用案件股票的行情条目，没有时引用市场状态条目。"""
    quote = _quote(snapshot, _symbol(snapshot))
    cited = snapshot.market.entry_id if quote is None else quote.entry_id
    return stand_in_signal(snapshot, (cited,), config)


def _reference_price(snapshot: Snapshot) -> Decimal:
    """参考价：案件股票的最新价；还没有成交时用前一交易日收盘价。没有行情条目时报错。"""
    quote = _quote(snapshot, _symbol(snapshot))
    if quote is None:
        raise ValueError(f"快照里没有 {snapshot.symbol} 的行情，替身不能开仓或升级")
    return quote.previous_close if quote.last_price is None else quote.last_price


def _quote(snapshot: Snapshot, symbol: str) -> QuoteEntry | None:
    return next((q for q in snapshot.quotes if q.symbol == symbol), None)


def _symbol(snapshot: Snapshot) -> str:
    """针对单只股票的案件的股票。"""
    if snapshot.symbol is None:
        raise ValueError(f"{snapshot.case_type} 的快照没有股票")
    return snapshot.symbol
