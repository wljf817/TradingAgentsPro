"""案件队列：按一份快照决定启动哪些排队的案件、哪些不再运行、作废哪些方案。

规则见 case-queue spec。两个纯函数每次都从快照把整个决定重新算一遍，不依赖调用的时机；
队列存在哪里、什么时候调用、怎么执行决定由调用方（M4 的工作流）负责。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.scheduler.config import SchedulerConfig
from tradingagentspro.modules.scheduler.domain.case_type import (
    CaseDefinition,
    Trigger,
    define_case,
)


class PlanStage(StrEnum):
    """案件给出的、还没提交订单的方案所处的阶段。"""

    REVIEW = "review"  # 审核：Critic 审核、风控检查，还没交给人工确认
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # 等人工确认
    AWAITING_ORDER = "awaiting_order"  # 已经确认，等开盘或下单前再校验


class QueueExit(StrEnum):
    """排队的案件怎样离开队列。"""

    STARTED = "started"  # 开始运行
    DROPPED = "dropped"  # 不再运行


@dataclass(frozen=True, slots=True)
class QueuedCase:
    """排队的案件。"""

    case_id: UUID
    trigger: Trigger
    triggered_at: datetime  # 触发时刻，带时区


@dataclass(frozen=True, slots=True)
class RunningCase:
    """运行中的案件。"""

    case_id: UUID
    case_type: CaseType  # 开始时确定的类型
    symbol: str | None  # 寻股和每周复盘没有
    # 还在调用模型的阶段：唤醒 PM 到 Critic 审核结束，复盘案件一直是；由「案件阶段」给出
    calling_models: bool


@dataclass(frozen=True, slots=True)
class PendingPlan:
    """案件给出的、还没提交订单的方案；手工录入的方案不放进来。"""

    plan_id: UUID
    symbol: str
    action: TradeAction
    stage: PlanStage


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """取得决定时的一份快照，由调用方从各模块组出。"""

    queued: tuple[QueuedCase, ...]  # 按入队的先后
    running: tuple[RunningCase, ...]
    plans: tuple[PendingPlan, ...]
    # 股票池里的股票和持仓的状态；不在里面的股票既不在股票池也不是持仓
    statuses: Mapping[str, StockStatus]
    exiting: frozenset[str]  # 正在紧急退出的股票：清仓卖单还没结束
    degraded: bool  # 处于降级模式：降级或等待对账
    general_budget_exhausted: bool  # 普通预算用完
    exploration_budget_exhausted: bool  # 寻股预算用完


@dataclass(frozen=True, slots=True)
class CaseStart:
    """启动一个排队的案件，用这次确定的类型。"""

    case_id: UUID
    definition: CaseDefinition


@dataclass(frozen=True, slots=True)
class QueueDecision:
    """队列的决定；调用方按作废、不再运行、启动的顺序执行。"""

    start: tuple[CaseStart, ...]  # 按启动的先后
    drop: tuple[UUID, ...]  # 不再运行的排队案件
    void: tuple[UUID, ...]  # 作废的方案


@dataclass(frozen=True, slots=True)
class EmergencyDecision:
    """一只股票紧急退出时的决定。"""

    abort: UUID | None  # 中止的案件；没有复盘以外的运行中案件时为 None
    void: tuple[UUID, ...]  # 作废的方案


_RETROSPECTIVES: Final = frozenset({CaseType.WEEKLY_RETROSPECTIVE, CaseType.SELL_RETROSPECTIVE})
# 针对持仓状态的案件类型：普通预算用完后改用持仓保留预算
_HOLDING_CASES: Final = frozenset(
    {
        CaseType.HOLDING_RISK_EVENT,
        CaseType.HOLDING_EVENT,
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.HOLDING_FOLLOW_UP,
    }
)
# 已经交给人工确认的阶段：开仓类方案在这些阶段会被新排队的案件作废
_CONFIRMING: Final = frozenset({PlanStage.AWAITING_CONFIRMATION, PlanStage.AWAITING_ORDER})


def _budget_exhausted(case_type: CaseType, snapshot: QueueSnapshot) -> bool:
    """这种案件用的预算是否用完；持仓案件用完普通预算后改用持仓保留预算。"""
    if case_type is CaseType.EXPLORATION:
        return snapshot.exploration_budget_exhausted
    return snapshot.general_budget_exhausted and case_type not in _HOLDING_CASES


def _starts(
    snapshot: QueueSnapshot,
    waiting: list[tuple[QueuedCase, CaseDefinition]],
    config: SchedulerConfig,
) -> tuple[CaseStart, ...]:
    """按优先级、触发时刻、入队的先后依次检查，能启动的就启动，直到名额用完。"""
    # 复盘以外的案件各占一个位置：针对单只股票的占这只股票，寻股占 None，所以寻股一次一个
    busy = {r.symbol for r in snapshot.running if r.case_type not in _RETROSPECTIVES}
    busy |= snapshot.exiting
    slots = config.max_concurrent_cases - sum(r.calling_models for r in snapshot.running)
    starts: list[CaseStart] = []
    # sorted 是稳定排序：优先级和触发时刻都相同时保持入队的先后
    for queued, definition in sorted(waiting, key=lambda w: (w[1].priority, w[0].triggered_at)):
        if len(starts) >= slots:
            break
        if _budget_exhausted(definition.case_type, snapshot):
            continue
        if definition.case_type not in _RETROSPECTIVES:
            if definition.symbol in busy:
                continue
            busy.add(definition.symbol)
        starts.append(CaseStart(queued.case_id, definition))
    return tuple(starts)


def queue_decision(snapshot: QueueSnapshot, config: SchedulerConfig) -> QueueDecision:
    """启动哪些案件、哪些排队的案件不再运行、作废哪些方案，规则见 case-queue spec。"""
    waiting: list[tuple[QueuedCase, CaseDefinition]] = []
    drop: list[UUID] = []
    for queued in snapshot.queued:
        symbol = queued.trigger.symbol
        status = None if symbol is None else snapshot.statuses.get(symbol)
        definition = define_case(queued.trigger, status)
        if definition is None:
            drop.append(queued.case_id)
        else:
            waiting.append((queued, definition))
    # 有复盘以外的案件在排队的股票：暂时启动不了的、这次启动的都算
    queued_symbols = {
        d.symbol for _, d in waiting if d.symbol is not None and d.case_type not in _RETROSPECTIVES
    }
    void = tuple(
        p.plan_id
        for p in snapshot.plans
        if p.action.is_opening and p.stage in _CONFIRMING and p.symbol in queued_symbols
    )
    start = () if snapshot.degraded else _starts(snapshot, waiting, config)
    return QueueDecision(start=start, drop=tuple(drop), void=void)


def emergency_decision(
    running: tuple[RunningCase, ...], plans: tuple[PendingPlan, ...], symbol: str
) -> EmergencyDecision:
    """symbol 触发紧急风险退出时，中止哪个案件、作废哪些方案。

    只看运行中的案件和还没提交订单的方案：排队的案件不动，降级模式、预算和并发上限都不看。
    """
    # 一只股票最多一个复盘以外的案件在运行；复盘案件不中止
    abort = next(
        (r.case_id for r in running if r.symbol == symbol and r.case_type not in _RETROSPECTIVES),
        None,
    )
    # 这只股票的方案不论动作和阶段全部作废
    void = tuple(p.plan_id for p in plans if p.symbol == symbol)
    return EmergencyDecision(abort=abort, void=void)
