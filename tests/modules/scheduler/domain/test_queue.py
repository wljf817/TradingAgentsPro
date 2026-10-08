"""队列的决定和紧急退出的决定的测试。

场景见 openspec 的 case-queue spec，每个场景一个测试。基准数据见 builders：AAA、DDD 是候选，
BBB 是关注，CCC、EEE 是持仓；触发时刻都在 2026-10-14（UTC）；排队的案件按列出的顺序入队。
spec 用触发称呼排队的案件、用案件类型称呼运行中的案件，测试里的案件 ID 按同样的名字取。
"""

from uuid import UUID

import pytest

from tests.modules.scheduler.builders import (
    P_1,
    P_2,
    P_3,
    POOL_SHORT,
    STATUSES,
    WEEKLY,
    at,
    case_id,
    follow_up,
    holding_review_due,
    new_candidate,
    news,
    plan,
    price_volume,
    queued,
    running,
    sell_fill,
    snapshot,
    statuses,
    watch_review_due,
)
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    CaseStart,
    EmergencyDecision,
    HeartbeatTrigger,
    PlanStage,
    QueueDecision,
    SchedulerConfig,
    Trigger,
    define_case,
    emergency_decision,
    queue_decision,
)

DEFAULT = SchedulerConfig()
LIMIT_1 = SchedulerConfig(max_concurrent_cases=1)
NOTHING = QueueDecision(start=(), drop=(), void=())
HOLDING, CANDIDATE = StockStatus.HOLDING, StockStatus.CANDIDATE
REVIEW, AWAITING_CONFIRMATION, AWAITING_ORDER = (
    PlanStage.REVIEW,
    PlanStage.AWAITING_CONFIRMATION,
    PlanStage.AWAITING_ORDER,
)


def _started(decision: QueueDecision) -> list[tuple[UUID, CaseType]]:
    """启动的案件和它这次确定的类型，按启动的先后。"""
    return [(start.case_id, start.definition.case_type) for start in decision.start]


def _definition(trigger: Trigger) -> CaseDefinition:
    """基准状态下这个触发开出的案件。"""
    status = None if trigger.symbol is None else STATUSES.get(trigger.symbol)
    definition = define_case(trigger, status)
    assert definition is not None
    return definition


# 队列的输入和决定


def test_empty_queue() -> None:
    """spec「队列为空」。"""
    assert queue_decision(snapshot(), DEFAULT) == NOTHING


def test_started_case_carries_the_type_decided_at_start() -> None:
    """spec「启动的案件带开始时确定的类型」。"""
    decision = queue_decision(
        snapshot([queued("BBB 的关注复查日到期", watch_review_due("BBB"), at(20))]), DEFAULT
    )

    assert decision == QueueDecision(
        start=(
            CaseStart(
                case_id("BBB 的关注复查日到期"),
                CaseDefinition(CaseType.WATCH_REVIEW, "BBB", "是否还值得关注？", 5, None),
            ),
        ),
        drop=(),
        void=(),
    )


# 排队的案件按现在的状态确定类型


def test_queued_case_is_ranked_by_its_type_under_the_new_status() -> None:
    """spec「排队期间状态变了按新的类型排」：BBB 买入成交、变为持仓。"""
    queue = [
        queued("AAA 的新闻事件", news("AAA"), at(13)),
        queued("BBB 的新闻事件", news("BBB"), at(14)),
    ]

    decision = queue_decision(snapshot(queue, statuses=statuses(BBB=HOLDING)), LIMIT_1)

    assert _started(decision) == [(case_id("BBB 的新闻事件"), CaseType.HOLDING_EVENT)]
    assert decision.start[0].definition.priority == 2
    assert decision.drop == ()  # AAA 的新闻事件留在队列里


def test_case_without_a_type_is_dropped_at_once() -> None:
    """spec「确定不了类型的马上不再运行」：CCC 的止损单成交、变为候选。"""
    decision = queue_decision(
        snapshot(
            [queued("CCC 的持仓复查日到期", holding_review_due("CCC"), at(20))],
            [running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC")],
            statuses=statuses(CCC=CANDIDATE),
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(start=(), drop=(case_id("CCC 的持仓复查日到期"),), void=())


def test_event_of_a_stock_outside_the_pool_is_dropped() -> None:
    """spec「不在股票池的股票的事件不再运行」：AAA 被删除。"""
    decision = queue_decision(
        snapshot([queued("AAA 的新闻事件", news("AAA"), at(14))], statuses=statuses(AAA=None)),
        DEFAULT,
    )

    assert decision == QueueDecision(start=(), drop=(case_id("AAA 的新闻事件"),), void=())


# 启动的顺序


def test_cases_of_different_stocks_start_by_priority() -> None:
    """spec「不同股票按优先级启动」。"""
    queue = snapshot(
        [
            queued("AAA 的有新候选", new_candidate("AAA"), at(13)),
            queued("BBB 的新闻事件", news("BBB"), at(14)),
            queued("CCC 的新闻事件", news("CCC"), at(15)),
        ]
    )

    assert _started(queue_decision(queue, LIMIT_1)) == [
        (case_id("CCC 的新闻事件"), CaseType.HOLDING_EVENT)
    ]
    assert _started(queue_decision(queue, DEFAULT)) == [
        (case_id("CCC 的新闻事件"), CaseType.HOLDING_EVENT),
        (case_id("BBB 的新闻事件"), CaseType.WATCH_EVENT),
        (case_id("AAA 的有新候选"), CaseType.CANDIDATE_EVALUATION),
    ]


def test_same_priority_starts_by_trigger_time() -> None:
    """spec「同优先级按触发时刻」。"""
    queue = snapshot(
        [
            queued("AAA 的新闻事件", news("AAA"), at(15)),
            queued("DDD 的价格和成交量异动事件", price_volume("DDD"), at(14)),
        ]
    )

    assert _started(queue_decision(queue, LIMIT_1)) == [
        (case_id("DDD 的价格和成交量异动事件"), CaseType.CANDIDATE_EVENT)
    ]


def test_same_trigger_time_starts_by_queue_order() -> None:
    """spec「触发时刻相同按入队的先后」：DDD 先入队，股票代码排在 AAA 之后。"""
    queue = snapshot(
        [
            queued("DDD 的有新候选", new_candidate("DDD"), at(20)),
            queued("AAA 的有新候选", new_candidate("AAA"), at(20)),
        ]
    )

    assert _started(queue_decision(queue, LIMIT_1)) == [
        (case_id("DDD 的有新候选"), CaseType.CANDIDATE_EVALUATION)
    ]


# 并发上限


def test_concurrency_limit_boundary() -> None:
    """spec「并发上限的边界」：两个调用模型的案件剩一个名额，三个没有名额。"""
    queue = [
        queued("BBB 的新闻事件", news("BBB"), at(14)),
        queued("AAA 的有新候选", new_candidate("AAA"), at(20)),
    ]
    review = running("EEE 的持仓复查", CaseType.HOLDING_REVIEW, "EEE")
    exploration = running("寻股", CaseType.EXPLORATION, None)
    weekly = running("每周复盘", CaseType.WEEKLY_RETROSPECTIVE, None)

    two_running = queue_decision(snapshot(queue, [review, exploration]), DEFAULT)
    three_running = queue_decision(snapshot(queue, [review, exploration, weekly]), DEFAULT)

    assert _started(two_running) == [(case_id("BBB 的新闻事件"), CaseType.WATCH_EVENT)]
    assert three_running == NOTHING


def test_cases_awaiting_confirmation_do_not_count() -> None:
    """spec「等人工确认的案件不计入」。"""
    decision = queue_decision(
        snapshot(
            [
                queued("AAA 的有新候选", new_candidate("AAA"), at(20)),
                queued("DDD 的有新候选", new_candidate("DDD"), at(20)),
            ],
            [
                running("EEE 的持仓复查", CaseType.HOLDING_REVIEW, "EEE"),
                running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC", calling_models=False),
                running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB", calling_models=False),
            ],
            [plan(P_3, AWAITING_CONFIRMATION), plan(P_1, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert _started(decision) == [
        (case_id("AAA 的有新候选"), CaseType.CANDIDATE_EVALUATION),
        (case_id("DDD 的有新候选"), CaseType.CANDIDATE_EVALUATION),
    ]


def test_retrospectives_count() -> None:
    """spec「复盘案件计入」。"""
    decision = queue_decision(
        snapshot(
            [queued("BBB 的新闻事件", news("BBB"), at(14))],
            [
                running("CCC 的卖出复盘", CaseType.SELL_RETROSPECTIVE, "CCC"),
                running("每周复盘", CaseType.WEEKLY_RETROSPECTIVE, None),
                running("EEE 的持仓复查", CaseType.HOLDING_REVIEW, "EEE"),
            ],
        ),
        DEFAULT,
    )

    assert decision == NOTHING


def test_over_the_limit_does_not_abort() -> None:
    """spec「超出上限不中止」：队列的决定里没有中止，四个运行中的案件照常运行。"""
    decision = queue_decision(
        snapshot(
            [queued("BBB 的新闻事件", news("BBB"), at(14))],
            [
                running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC"),
                running("EEE 的持仓复查", CaseType.HOLDING_REVIEW, "EEE"),
                running("寻股", CaseType.EXPLORATION, None),
                running("每周复盘", CaseType.WEEKLY_RETROSPECTIVE, None),
            ],
        ),
        DEFAULT,
    )

    assert decision == NOTHING


# 队列配置


def test_limit_of_one() -> None:
    """spec「改成 1」。"""
    queue = snapshot(
        [
            queued("BBB 的新闻事件", news("BBB"), at(14)),
            queued("AAA 的新闻事件", news("AAA"), at(15)),
        ]
    )

    assert _started(queue_decision(queue, LIMIT_1)) == [
        (case_id("BBB 的新闻事件"), CaseType.WATCH_EVENT)
    ]


# 同一只股票一次一个案件


def test_busy_stock_is_skipped_without_blocking_other_stocks() -> None:
    """spec「股票忙时跳过，不挡别的股票」。"""
    decision = queue_decision(
        snapshot(
            [
                queued(
                    "CCC 的价格和成交量异动事件", price_volume("CCC", holding_risk=True), at(14)
                ),
                queued("BBB 的新闻事件", news("BBB"), at(15)),
            ],
            [running("CCC 的持仓复查", CaseType.HOLDING_REVIEW, "CCC")],
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(
        start=(CaseStart(case_id("BBB 的新闻事件"), _definition(news("BBB"))),),
        drop=(),
        void=(),
    )


def test_case_awaiting_confirmation_still_holds_its_stock() -> None:
    """spec「等人工确认的案件也占着这只股票」：名额没满也不启动。"""
    decision = queue_decision(
        snapshot(
            [queued("CCC 的持仓复查日到期", holding_review_due("CCC"), at(20))],
            [running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC", calling_models=False)],
            [plan(P_3, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert decision == NOTHING


def test_queued_cases_of_one_stock_run_one_by_one() -> None:
    """spec「同一只股票排队的案件依次运行」。"""
    review = queued("CCC 的持仓复查日到期", holding_review_due("CCC"), at(20))
    event = queued("CCC 的新闻事件", news("CCC"), at(14))

    both_queued = queue_decision(snapshot([review, event]), DEFAULT)
    after_the_event = queue_decision(snapshot([review]), DEFAULT)

    assert _started(both_queued) == [(case_id("CCC 的新闻事件"), CaseType.HOLDING_EVENT)]
    assert _started(after_the_event) == [(case_id("CCC 的持仓复查日到期"), CaseType.HOLDING_REVIEW)]


def test_stock_in_an_emergency_exit() -> None:
    """spec「正在紧急退出的股票」：清仓卖单成交以后 CCC 变为候选，紧急退出结束。"""
    ccc_follow_up = queued("CCC 的跟进", follow_up("CCC"), at(13))
    ccc_event = queued("CCC 的价格和成交量异动事件", price_volume("CCC", holding_risk=True), at(15))
    bbb_event = queued("BBB 的新闻事件", news("BBB"), at(15, 1))

    exiting = queue_decision(
        snapshot([ccc_follow_up, ccc_event, bbb_event], exiting={"CCC"}), DEFAULT
    )
    exit_over = queue_decision(
        snapshot(
            [ccc_follow_up, ccc_event],
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB")],
            statuses=statuses(CCC=CANDIDATE),
        ),
        DEFAULT,
    )

    assert exiting == QueueDecision(
        start=(CaseStart(case_id("BBB 的新闻事件"), _definition(news("BBB"))),),
        drop=(),
        void=(),
    )
    assert exit_over.drop == (case_id("CCC 的跟进"),)
    assert _started(exit_over) == [
        (case_id("CCC 的价格和成交量异动事件"), CaseType.CANDIDATE_EVENT)
    ]


def test_one_exploration_at_a_time() -> None:
    """spec「寻股同一时间只跑一个」。"""
    exploration_running = queue_decision(
        snapshot(
            [queued("股票池候选不足", POOL_SHORT, at(20))],
            [running("寻股", CaseType.EXPLORATION, None)],
        ),
        DEFAULT,
    )
    two_queued = queue_decision(
        snapshot(
            [
                queued("14:00Z 的股票池候选不足", POOL_SHORT, at(14)),
                queued("20:00Z 的股票池候选不足", POOL_SHORT, at(20)),
            ]
        ),
        DEFAULT,
    )

    assert exploration_running == NOTHING
    assert _started(two_queued) == [(case_id("14:00Z 的股票池候选不足"), CaseType.EXPLORATION)]


def test_retrospectives_are_not_limited_by_the_stock() -> None:
    """spec「复盘案件不受限制」：不看这只股票忙不忙、在不在紧急退出，也不挡这只股票的其他案件。"""
    sell_fill_ccc = queued("CCC 的卖出成交", sell_fill("CCC"), at(15))

    ccc_busy = queue_decision(
        snapshot(
            [sell_fill_ccc, queued("每周固定时间", WEEKLY, at(20))],
            [running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC")],
        ),
        DEFAULT,
    )
    retrospective_running = queue_decision(
        snapshot(
            [queued("CCC 的新闻事件", news("CCC"), at(15))],
            [running("CCC 的卖出复盘", CaseType.SELL_RETROSPECTIVE, "CCC")],
        ),
        DEFAULT,
    )
    ccc_exiting = queue_decision(snapshot([sell_fill_ccc], exiting={"CCC"}), DEFAULT)

    assert _started(ccc_busy) == [
        (case_id("CCC 的卖出成交"), CaseType.SELL_RETROSPECTIVE),
        (case_id("每周固定时间"), CaseType.WEEKLY_RETROSPECTIVE),
    ]
    assert _started(retrospective_running) == [(case_id("CCC 的新闻事件"), CaseType.HOLDING_EVENT)]
    assert _started(ccc_exiting) == [(case_id("CCC 的卖出成交"), CaseType.SELL_RETROSPECTIVE)]


# 降级模式

_BACKLOG = [
    queued("AAA 的有新候选", new_candidate("AAA"), at(20)),
    queued("BBB 的关注复查日到期", watch_review_due("BBB"), at(20)),
    queued("CCC 的持仓复查日到期", holding_review_due("CCC"), at(20)),
]


def test_no_start_while_degraded() -> None:
    """spec「降级期间不启动」：EEE 的持仓复查照常运行，队列的决定里没有中止。"""
    decision = queue_decision(
        snapshot(
            _BACKLOG,
            [running("EEE 的持仓复查", CaseType.HOLDING_REVIEW, "EEE")],
            degraded=True,
        ),
        DEFAULT,
    )

    assert decision == NOTHING


def test_backlog_runs_by_priority_after_recovery() -> None:
    """spec「恢复后按优先级运行积压的案件」：EEE 的持仓复查已经结束。"""
    recovered = snapshot(_BACKLOG)

    assert _started(queue_decision(recovered, DEFAULT)) == [
        (case_id("CCC 的持仓复查日到期"), CaseType.HOLDING_REVIEW),
        (case_id("BBB 的关注复查日到期"), CaseType.WATCH_REVIEW),
        (case_id("AAA 的有新候选"), CaseType.CANDIDATE_EVALUATION),
    ]
    assert _started(queue_decision(recovered, SchedulerConfig(max_concurrent_cases=2))) == [
        (case_id("CCC 的持仓复查日到期"), CaseType.HOLDING_REVIEW),
        (case_id("BBB 的关注复查日到期"), CaseType.WATCH_REVIEW),
    ]


# 调用预算


def test_holding_cases_start_after_the_general_budget_runs_out() -> None:
    """spec「普通预算用完后持仓案件照常启动」。"""
    decision = queue_decision(
        snapshot(
            [
                queued(
                    "CCC 的价格和成交量异动事件", price_volume("CCC", holding_risk=True), at(14)
                ),
                queued("EEE 的持仓复查日到期", holding_review_due("EEE"), at(20)),
            ],
            general_budget_exhausted=True,
        ),
        DEFAULT,
    )

    assert _started(decision) == [
        (case_id("CCC 的价格和成交量异动事件"), CaseType.HOLDING_RISK_EVENT),
        (case_id("EEE 的持仓复查日到期"), CaseType.HOLDING_REVIEW),
    ]


def test_watch_and_candidate_cases_wait_after_the_general_budget_runs_out() -> None:
    """spec「普通预算用完后关注和候选的案件留在队列里」。"""
    decision = queue_decision(
        snapshot(
            [
                queued("BBB 的新闻事件", news("BBB"), at(14)),
                queued("AAA 的有新候选", new_candidate("AAA"), at(20)),
            ],
            general_budget_exhausted=True,
        ),
        DEFAULT,
    )

    assert decision == NOTHING


def test_general_budget_does_not_block_exploration() -> None:
    """spec「普通预算用完不挡寻股」：留在队列里的 BBB 的新闻事件不占名额。"""
    decision = queue_decision(
        snapshot(
            [
                queued("BBB 的新闻事件", news("BBB"), at(14)),
                queued("股票池候选不足", POOL_SHORT, at(20)),
            ],
            general_budget_exhausted=True,
        ),
        LIMIT_1,
    )

    assert _started(decision) == [(case_id("股票池候选不足"), CaseType.EXPLORATION)]


def test_exploration_budget_runs_out() -> None:
    """spec「寻股预算用完」。"""
    decision = queue_decision(
        snapshot(
            [
                queued("股票池候选不足", POOL_SHORT, at(14)),
                queued("AAA 的有新候选", new_candidate("AAA"), at(20)),
            ],
            exploration_budget_exhausted=True,
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(
        start=(CaseStart(case_id("AAA 的有新候选"), _definition(new_candidate("AAA"))),),
        drop=(),
        void=(),
    )


def test_retrospectives_use_the_general_budget() -> None:
    """spec「复盘用普通预算」。"""
    decision = queue_decision(
        snapshot(
            [
                queued("CCC 的卖出成交", sell_fill("CCC"), at(15)),
                queued("每周固定时间", WEEKLY, at(20)),
            ],
            general_budget_exhausted=True,
        ),
        DEFAULT,
    )

    assert decision == NOTHING


def test_backlog_runs_by_priority_after_the_budget_recovers() -> None:
    """spec「预算恢复后按优先级运行」。"""
    queue = [
        queued("每周固定时间", WEEKLY, at(20)),
        queued("AAA 的有新候选", new_candidate("AAA"), at(20)),
        queued("BBB 的新闻事件", news("BBB"), at(14)),
    ]

    exhausted = queue_decision(snapshot(queue, general_budget_exhausted=True), DEFAULT)
    recovered = queue_decision(snapshot(queue), DEFAULT)

    assert exhausted == NOTHING
    assert _started(recovered) == [
        (case_id("BBB 的新闻事件"), CaseType.WATCH_EVENT),
        (case_id("AAA 的有新候选"), CaseType.CANDIDATE_EVALUATION),
        (case_id("每周固定时间"), CaseType.WEEKLY_RETROSPECTIVE),
    ]


# spec「调用预算」的表：基准状态下开出这种类型的触发，普通预算用完、寻股预算用完时能不能启动
_BUDGET_TABLE: dict[CaseType, tuple[Trigger, bool, bool]] = {
    CaseType.HOLDING_RISK_EVENT: (price_volume("CCC", holding_risk=True), True, True),
    CaseType.HOLDING_EVENT: (news("CCC"), True, True),
    CaseType.HOLDING_REVIEW: (holding_review_due("CCC"), True, True),
    CaseType.HOLDING_CATALYST_REVIEW: (
        HeartbeatTrigger(CaseType.HOLDING_CATALYST_REVIEW, "CCC"),
        True,
        True,
    ),
    CaseType.HOLDING_FOLLOW_UP: (follow_up("CCC"), True, True),
    CaseType.WATCH_EVENT: (news("BBB"), False, True),
    CaseType.WATCH_REVIEW: (watch_review_due("BBB"), False, True),
    CaseType.WATCH_CATALYST_REVIEW: (
        HeartbeatTrigger(CaseType.WATCH_CATALYST_REVIEW, "BBB"),
        False,
        True,
    ),
    CaseType.WATCH_FOLLOW_UP: (follow_up("BBB"), False, True),
    CaseType.CANDIDATE_EVENT: (news("AAA"), False, True),
    CaseType.CANDIDATE_EVALUATION: (new_candidate("AAA"), False, True),
    CaseType.EXPLORATION: (POOL_SHORT, True, False),
    CaseType.WEEKLY_RETROSPECTIVE: (WEEKLY, False, True),
    CaseType.SELL_RETROSPECTIVE: (sell_fill("CCC"), False, True),
}


def test_budget_table_covers_every_case_type() -> None:
    assert set(_BUDGET_TABLE) == set(CaseType)


@pytest.mark.parametrize(
    ("case_type", "trigger", "after_general", "after_exploration"),
    [pytest.param(case_type, *row, id=case_type.value) for case_type, row in _BUDGET_TABLE.items()],
)
def test_budget_flags_by_case_type(
    case_type: CaseType, trigger: Trigger, after_general: bool, after_exploration: bool
) -> None:
    """spec「调用预算」的表：每种案件类型在普通预算用完、寻股预算用完时能不能启动。"""
    queue = [queued("排队的案件", trigger, at(14))]

    budget_left = queue_decision(snapshot(queue), DEFAULT)
    general_exhausted = queue_decision(snapshot(queue, general_budget_exhausted=True), DEFAULT)
    exploration_exhausted = queue_decision(
        snapshot(queue, exploration_budget_exhausted=True), DEFAULT
    )

    assert _started(budget_left) == [(case_id("排队的案件"), case_type)]
    assert _started(general_exhausted) == (_started(budget_left) if after_general else [])
    assert _started(exploration_exhausted) == (_started(budget_left) if after_exploration else [])


# 新案件排队时作废开仓方案


def test_opening_plan_in_review_is_not_voided() -> None:
    """spec「还在审核的开仓方案不作废」。"""
    decision = queue_decision(
        snapshot(
            [queued("BBB 的关注复查日到期", watch_review_due("BBB"), at(20))],
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB")],
            [plan(P_1, REVIEW)],
        ),
        DEFAULT,
    )

    assert decision == NOTHING


def test_new_case_voids_a_plan_awaiting_confirmation() -> None:
    """spec「等人工确认时有新案件排队就作废」：BBB 的新闻事件等 BBB 的关注股事件结束。"""
    decision = queue_decision(
        snapshot(
            [queued("BBB 的新闻事件", news("BBB"), at(15))],
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB", calling_models=False)],
            [plan(P_1, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(start=(), drop=(), void=(P_1,))


def test_confirmed_opening_plan_is_voided_too() -> None:
    """spec「已经确认的开仓方案也作废」：BBB 没有运行中的案件。"""
    decision = queue_decision(
        snapshot(
            [queued("BBB 的关注复查日到期", watch_review_due("BBB"), at(20))],
            plans=[plan(P_1, AWAITING_ORDER)],
        ),
        DEFAULT,
    )

    assert decision.void == (P_1,)
    assert _started(decision) == [(case_id("BBB 的关注复查日到期"), CaseType.WATCH_REVIEW)]


def test_plan_reaching_confirmation_with_a_case_already_queued() -> None:
    """spec「方案进入等人工确认时已经有案件在排队」。"""
    queue = [queued("BBB 的新闻事件", news("BBB"), at(15))]

    in_review = queue_decision(
        snapshot(
            queue,
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB")],
            [plan(P_1, REVIEW)],
        ),
        DEFAULT,
    )
    awaiting_confirmation = queue_decision(
        snapshot(
            queue,
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB", calling_models=False)],
            [plan(P_1, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert in_review == NOTHING
    assert awaiting_confirmation == QueueDecision(start=(), drop=(), void=(P_1,))


def test_adding_to_a_position_is_opening() -> None:
    """spec「加仓也是开仓类」。"""
    decision = queue_decision(
        snapshot(
            [queued("CCC 的新闻事件", news("CCC"), at(15))],
            [running("CCC 的持仓复查", CaseType.HOLDING_REVIEW, "CCC", calling_models=False)],
            [plan(P_2, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(start=(), drop=(), void=(P_2,))


def test_queued_retrospective_alone_does_not_void() -> None:
    """spec「只有复盘在排队不作废」。"""
    decision = queue_decision(
        snapshot(
            [queued("CCC 的卖出成交", sell_fill("CCC"), at(15))],
            [running("CCC 的持仓复查", CaseType.HOLDING_REVIEW, "CCC", calling_models=False)],
            [plan(P_2, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(
        start=(CaseStart(case_id("CCC 的卖出成交"), _definition(sell_fill("CCC"))),),
        drop=(),
        void=(),
    )


def test_exit_plans_are_not_voided() -> None:
    """spec「退出类方案不作废」：P-3 确认、进入等下单，CCC 的持仓事件结束以后才启动。"""
    queue = [queued("CCC 的持仓复查日到期", holding_review_due("CCC"), at(20))]

    awaiting_confirmation = queue_decision(
        snapshot(
            queue,
            [running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC", calling_models=False)],
            [plan(P_3, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )
    case_over = queue_decision(snapshot(queue, plans=[plan(P_3, AWAITING_ORDER)]), DEFAULT)

    assert awaiting_confirmation == NOTHING
    assert case_over.void == ()
    assert _started(case_over) == [(case_id("CCC 的持仓复查日到期"), CaseType.HOLDING_REVIEW)]


def test_case_that_cannot_start_yet_still_voids() -> None:
    """spec「暂时启动不了的排队案件也算」：普通预算用完。"""
    decision = queue_decision(
        snapshot(
            [queued("BBB 的关注复查日到期", watch_review_due("BBB"), at(20))],
            plans=[plan(P_1, AWAITING_ORDER)],
            general_budget_exhausted=True,
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(start=(), drop=(), void=(P_1,))


def test_case_without_a_type_does_not_void() -> None:
    """spec「确定不了类型的排队案件不算」：BBB 还是候选时开出有新候选，之后升级为关注。"""
    decision = queue_decision(
        snapshot(
            [queued("BBB 的有新候选", new_candidate("BBB"), at(20))],
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB", calling_models=False)],
            [plan(P_1, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(start=(), drop=(case_id("BBB 的有新候选"),), void=())


def test_cases_of_other_stocks_do_not_void() -> None:
    """spec「别的股票的案件不影响」。"""
    decision = queue_decision(
        snapshot(
            [queued("AAA 的新闻事件", news("AAA"), at(15))],
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB", calling_models=False)],
            [plan(P_1, AWAITING_CONFIRMATION)],
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(
        start=(CaseStart(case_id("AAA 的新闻事件"), _definition(news("AAA"))),),
        drop=(),
        void=(),
    )


def test_drop_and_void_go_on_while_degraded() -> None:
    """spec「降级期间照常不再运行和作废」：CCC 的止损单成交、变为候选。"""
    decision = queue_decision(
        snapshot(
            [
                queued("BBB 的新闻事件", news("BBB"), at(14)),
                queued("CCC 的持仓复查日到期", holding_review_due("CCC"), at(20)),
            ],
            plans=[plan(P_1, AWAITING_ORDER)],
            statuses=statuses(CCC=CANDIDATE),
            degraded=True,
        ),
        DEFAULT,
    )

    assert decision == QueueDecision(start=(), drop=(case_id("CCC 的持仓复查日到期"),), void=(P_1,))


# 紧急退出


def test_emergency_aborts_the_running_case_and_voids_its_plan() -> None:
    """spec「中止运行中的案件并作废它的方案」。"""
    decision = emergency_decision(
        snapshot(
            running=[
                running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC", calling_models=False)
            ],
            plans=[plan(P_3, AWAITING_CONFIRMATION)],
        ),
        "CCC",
    )

    assert decision == EmergencyDecision(abort=case_id("CCC 的持仓事件"), void=(P_3,))


def test_emergency_voids_plans_of_every_action_and_stage() -> None:
    """spec「开仓类和退出类、各个阶段的方案都作废」：P-2 所属的案件已经结束。"""
    decision = emergency_decision(
        snapshot(
            running=[running("CCC 的持仓复查", CaseType.HOLDING_REVIEW, "CCC")],
            plans=[plan(P_3, REVIEW), plan(P_2, AWAITING_ORDER)],
        ),
        "CCC",
    )

    assert decision.abort == case_id("CCC 的持仓复查")
    assert sorted(decision.void) == sorted([P_2, P_3])


def test_emergency_does_not_abort_a_retrospective() -> None:
    """spec「复盘案件不中止」。"""
    decision = emergency_decision(
        snapshot(
            running=[running("CCC 的卖出复盘", CaseType.SELL_RETROSPECTIVE, "CCC")],
            plans=[plan(P_2, AWAITING_ORDER)],
        ),
        "CCC",
    )

    assert decision == EmergencyDecision(abort=None, void=(P_2,))


def test_emergency_leaves_queued_cases_and_other_stocks() -> None:
    """spec「排队的案件和别的股票不动」：决定里没有排队的案件，CCC 的新闻事件留在队列里。"""
    decision = emergency_decision(
        snapshot(
            [queued("CCC 的新闻事件", news("CCC"), at(15))],
            [running("BBB 的关注股事件", CaseType.WATCH_EVENT, "BBB", calling_models=False)],
            [plan(P_1, AWAITING_CONFIRMATION)],
        ),
        "CCC",
    )

    assert decision == EmergencyDecision(abort=None, void=())


def test_emergency_ignores_degraded_mode_and_budgets() -> None:
    """spec「降级模式和预算用完时照常」。"""
    decision = emergency_decision(
        snapshot(
            running=[
                running("CCC 的持仓事件", CaseType.HOLDING_EVENT, "CCC", calling_models=False)
            ],
            plans=[plan(P_3, AWAITING_CONFIRMATION)],
            degraded=True,
            general_budget_exhausted=True,
        ),
        "CCC",
    )

    assert decision == EmergencyDecision(abort=case_id("CCC 的持仓事件"), void=(P_3,))
