"""跟进审批用例的测试。每个测试对应 openspec 的 follow-ups spec 里的一个场景，说明里写明场景。

基准数据见 tests/modules/followups/builders.py。spec 一个 WHEN 里用"；"或"或者"隔开的几种情况
各自从 GIVEN 开始，测试里各用一个新的用例。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from tests.fakes.clock import FakeClock
from tests.fakes.follow_ups import InMemoryFollowUpStore
from tests.modules.followups.builders import (
    BASELINE_NOW,
    BBB_CATALYST,
    CATALYSTS,
    CCC_CATALYST,
    E1,
    EVENT_E1,
    EVENT_REQUEST,
    F1,
    F2,
    F3,
    K1,
    K2,
    OBSERVATION_REQUEST,
    PRICE_55,
    PRICE_56,
    S9,
    S10,
    SCHEDULE_REQUEST,
    V7,
    at,
    changed,
    follow_up_service,
)
from tradingagentspro.contracts.conclusion import FollowUpReason, FollowUpRequest
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import Catalyst, CatalystReference, Condition
from tradingagentspro.modules.followups import (
    FollowUp,
    FollowUpRejection,
    FollowUpStatus,
)

PENDING, TRIGGERED = FollowUpStatus.PENDING, FollowUpStatus.TRIGGERED
REPLACED, VOIDED = FollowUpStatus.REPLACED, FollowUpStatus.VOIDED
EVENT, SCHEDULE, OBSERVATION = (
    FollowUpReason.EVENT_REACTION,
    FollowUpReason.EXTERNAL_SCHEDULE,
    FollowUpReason.OBSERVATION,
)
CANDIDATE, WATCH, HOLDING = StockStatus.CANDIDATE, StockStatus.WATCH, StockStatus.HOLDING
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)  # 早于一切记录：requested_since(EPOCH) 是全部记录


class _Setup:
    """一个跟进审批用例和它的时钟、存储。"""

    def __init__(self, now: datetime = BASELINE_NOW, **config: Any) -> None:
        self.clock = FakeClock(now)
        self.store = InMemoryFollowUpStore()
        self.service = follow_up_service(self.clock, self.store, **config)

    def apply(
        self,
        symbol: str = "CCC",
        request: FollowUpRequest = SCHEDULE_REQUEST,
        *,
        case_id: UUID = K1,
        catalyst: Catalyst | None = None,
    ) -> FollowUp | FollowUpRejection:
        """spec 里的"CCC 申请日程申请"：这只股票的案件的结论附了申请，审批时传入这只股票的
        催化剂。
        """
        given = CATALYSTS[symbol] if catalyst is None else catalyst
        return self.service.request(symbol, request, case_id, given)

    def approve(self, symbol: str = "CCC", request: FollowUpRequest = SCHEDULE_REQUEST) -> FollowUp:
        """GIVEN 里一定获批的申请。"""
        return _approved(self.apply(symbol, request))

    def trigger_at(self, now: datetime) -> list[UUID]:
        """当前时刻改为 now，到点触发，给出这次触发的跟进。"""
        self.clock.set(now)
        return [f.follow_up_id for f in self.service.trigger_due()]

    def status(self, follow_up_id: UUID) -> FollowUpStatus:
        return self.store.get(follow_up_id).status

    def records(self) -> tuple[FollowUp, ...]:
        return self.store.requested_since(EPOCH)

    def pending_ids(self, symbol: str) -> list[UUID]:
        return [f.follow_up_id for f in self.service.pending(symbol)]


def _approved(result: FollowUp | FollowUpRejection) -> FollowUp:
    assert isinstance(result, FollowUp), result
    return result


# 跟进的记录


def test_approved_follow_up_record() -> None:
    """spec「获批的跟进的记录」。"""
    setup = _Setup()

    f1 = _approved(setup.apply("CCC", SCHEDULE_REQUEST))

    assert f1 == FollowUp(F1, "CCC", SCHEDULE_REQUEST, K1, BASELINE_NOW, PENDING)
    assert setup.service.pending("CCC") == (f1,)


def test_pending_in_trigger_order() -> None:
    """spec「按触发时间给出待执行的跟进」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)

    pending = setup.service.pending("CCC")

    assert [f.follow_up_id for f in pending] == [F2, F1]
    assert [f.request for f in pending] == [EVENT_REQUEST, SCHEDULE_REQUEST]
    assert setup.service.pending("BBB") == ()


# 审批的输入和结果


def test_rejection_changes_nothing() -> None:
    """spec「不通过时不改变记录」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    records = setup.records()

    result = setup.apply("CCC", changed(EVENT_REQUEST, reference=None))

    assert result is FollowUpRejection.NO_REFERENCE
    assert setup.pending_ids("CCC") == [F1]
    assert setup.records() == records


def test_compared_with_the_catalyst_after_the_conclusion() -> None:
    """spec「和结论生效以后的催化剂比」：附了论点更新时传入更新里的催化剂（只关联事件 V-7）。"""
    updated = BBB_CATALYST.model_copy(update={"reference": V7})

    rejected = _Setup().apply("BBB", EVENT_REQUEST, catalyst=updated)
    approved = _Setup().apply("BBB", EVENT_REQUEST, catalyst=BBB_CATALYST)

    assert rejected is FollowUpRejection.CATALYST_COVERED
    assert isinstance(approved, FollowUp)


# 审批的顺序


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (
            changed(EVENT_REQUEST, reference=None, trigger_at=at(15, day=13)),
            FollowUpRejection.NO_REFERENCE,
        ),
        (changed(EVENT_REQUEST, reference=E1), FollowUpRejection.REFERENCE_MISMATCH),
        (
            changed(SCHEDULE_REQUEST, reference=E1, trigger_at=at(15, day=13)),
            FollowUpRejection.CATALYST_COVERED,
        ),
    ],
    ids=["没有引用、触发时间已过", "原因和引用不符", "催化剂已经覆盖、触发时间已过"],
)
def test_first_failing_check(request_: FollowUpRequest, expected: FollowUpRejection) -> None:
    """spec「多项不通过时给出第一项」的前三个 WHEN。"""
    assert _Setup().apply("CCC", request_) is expected


def test_first_failing_check_stock_full_before_daily_limit() -> None:
    """spec「多项不通过时给出第一项」的最后一个 WHEN：已满和次数用完同时不通过。"""
    setup = _Setup(max_daily_requests=2)
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)

    assert setup.apply("CCC", OBSERVATION_REQUEST) is FollowUpRejection.STOCK_FULL


# 引用的对象


@pytest.mark.parametrize(
    ("symbol", "request_"),
    [("CCC", SCHEDULE_REQUEST), ("BBB", OBSERVATION_REQUEST)],
    ids=["CCC 日程申请", "BBB 观察申请"],
)
def test_no_reference(symbol: str, request_: FollowUpRequest) -> None:
    """spec「没有引用具体对象」。"""
    result = _Setup().apply(symbol, changed(request_, reference=None))

    assert result is FollowUpRejection.NO_REFERENCE


def test_each_reason_with_its_reference() -> None:
    """spec「三种原因各自引用对应的对象」。"""
    setup = _Setup()

    results = [
        setup.apply("CCC", EVENT_REQUEST),
        setup.apply("CCC", SCHEDULE_REQUEST),
        setup.apply("BBB", OBSERVATION_REQUEST),
    ]

    assert [_approved(r).follow_up_id for r in results] == [F1, F2, F3]
    assert all(_approved(r).status is PENDING for r in results)
    assert setup.pending_ids("CCC") == [F1, F2]
    assert setup.pending_ids("BBB") == [F3]


@pytest.mark.parametrize(
    ("reason", "reference"),
    [(EVENT, S9), (SCHEDULE, V7), (SCHEDULE, PRICE_55), (OBSERVATION, V7)],
    ids=[
        "事件后反应引用日程项",
        "外部日程引用事件",
        "外部日程引用条件",
        "价格或指标观察引用事件",
    ],
)
def test_reference_mismatch(
    reason: FollowUpReason, reference: CatalystReference | Condition
) -> None:
    """spec「原因和引用的对象不符」。"""
    request = changed(EVENT_REQUEST, reason=reason, reference=reference)

    assert _Setup().apply("CCC", request) is FollowUpRejection.REFERENCE_MISMATCH


# 不和催化剂重复


def test_catalyst_schedule_item() -> None:
    """spec「引用催化剂的日程项」。"""
    result = _Setup().apply("CCC", changed(SCHEDULE_REQUEST, reference=E1))

    assert result is FollowUpRejection.CATALYST_COVERED


def test_catalyst_linked_event() -> None:
    """spec「引用催化剂关联的事件」。"""
    catalyst = CCC_CATALYST.model_copy(update={"reference": V7})

    result = _Setup().apply("CCC", EVENT_REQUEST, catalyst=catalyst)

    assert result is FollowUpRejection.CATALYST_COVERED


@pytest.mark.parametrize(
    ("symbol", "request_"),
    [
        ("CCC", SCHEDULE_REQUEST),
        ("CCC", changed(EVENT_REQUEST, reference=EVENT_E1)),
        ("BBB", changed(SCHEDULE_REQUEST, reference=E1)),
        ("CCC", OBSERVATION_REQUEST),
    ],
    ids=["日程项 S-9", "事件 E-1", "BBB 的催化剂不引用", "引用条件"],
)
def test_different_objects_are_not_duplicates(symbol: str, request_: FollowUpRequest) -> None:
    """spec「不同的对象不算重复」。"""
    assert isinstance(_Setup().apply(symbol, request_), FollowUp)


# 触发时间


@pytest.mark.parametrize("trigger_at", [at(15), at(18, day=13)], ids=["等于现在", "过去"])
def test_trigger_not_after_now(trigger_at: datetime) -> None:
    """spec「触发时间在现在之后」：不通过。"""
    result = _Setup().apply("CCC", changed(EVENT_REQUEST, trigger_at=trigger_at))

    assert result is FollowUpRejection.TRIGGER_PASSED


def test_trigger_one_second_after_now() -> None:
    """spec「触发时间在现在之后」：晚 1 秒获批。"""
    result = _Setup().apply("CCC", changed(EVENT_REQUEST, trigger_at=at(15, 0, 1)))

    assert isinstance(result, FollowUp)


@pytest.mark.parametrize(
    ("trigger_at", "expected"),
    [
        (at(20, 59, day=11, month=1, year=2027), None),
        (at(15, day=10, month=1, year=2027), None),
        (at(21, day=11, month=1, year=2027), FollowUpRejection.TRIGGER_TOO_FAR),
        (at(14, 30, day=12, month=1, year=2027), FollowUpRejection.TRIGGER_TOO_FAR),
    ],
    ids=["2027-01-11 20:59Z", "周日 2027-01-10", "2027-01-11 21:00Z", "2027-01-12 14:30Z"],
)
def test_farthest_trigger_boundary(
    trigger_at: datetime, expected: FollowUpRejection | None
) -> None:
    """spec「最远间隔的边界」。"""
    result = _Setup().apply("CCC", changed(SCHEDULE_REQUEST, trigger_at=trigger_at))

    assert (result if isinstance(result, FollowUpRejection) else None) is expected


@pytest.mark.parametrize(
    ("trigger_at", "expected"),
    [
        (at(20, 59, day=12, month=1, year=2027), None),
        (at(21, day=12, month=1, year=2027), FollowUpRejection.TRIGGER_TOO_FAR),
    ],
    ids=["2027-01-12 20:59Z", "2027-01-12 21:00Z"],
)
def test_after_the_close_counts_from_the_next_session(
    trigger_at: datetime, expected: FollowUpRejection | None
) -> None:
    """spec「收盘后审批从下一个交易日算起」：当前时刻 2026-10-14 20:00Z。"""
    result = _Setup(at(20)).apply("CCC", changed(SCHEDULE_REQUEST, trigger_at=trigger_at))

    assert (result if isinstance(result, FollowUpRejection) else None) is expected


# 原因相同时替换


def test_new_request_replaces_the_old() -> None:
    """spec「用新申请替换旧的」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.clock.set(at(15, day=15))
    new = FollowUpRequest(
        reason=SCHEDULE,
        reference=S9,
        question="竞争对手财报推迟到 11 月 25 日，之后有什么影响？",
        possible_actions="减仓或清仓",
        trigger_at=at(14, 30, day=30, month=11),
    )

    f2 = _approved(setup.apply("CCC", new, case_id=K2))

    assert f2 == FollowUp(F2, "CCC", new, K2, at(15, day=15), PENDING)
    assert setup.status(F1) is REPLACED
    assert setup.pending_ids("CCC") == [F2]


def test_different_schedule_item_does_not_replace() -> None:
    """spec「引用不同的对象不替换」：日程项 S-10。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)

    f2 = _approved(setup.apply("CCC", changed(SCHEDULE_REQUEST, reference=S10)))

    assert f2.follow_up_id == F2
    assert setup.status(F1) is PENDING


def test_different_condition_does_not_replace() -> None:
    """spec「引用不同的对象不替换」：条件"价格不低于 56"。"""
    setup = _Setup()
    setup.approve("BBB", OBSERVATION_REQUEST)

    f2 = _approved(setup.apply("BBB", changed(OBSERVATION_REQUEST, reference=PRICE_56)))

    assert f2.follow_up_id == F2
    assert setup.status(F1) is PENDING


def test_other_stock_does_not_replace() -> None:
    """spec「别的股票的跟进不替换」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)

    f2 = _approved(setup.apply("BBB", SCHEDULE_REQUEST))

    assert f2.follow_up_id == F2
    assert setup.status(F1) is PENDING


def test_rejected_replacement_does_not_replace() -> None:
    """spec「替换的申请不通过时不替换」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)

    result = setup.apply(
        "CCC", changed(SCHEDULE_REQUEST, trigger_at=at(14, 30, day=12, month=1, year=2027))
    )

    assert result is FollowUpRejection.TRIGGER_TOO_FAR
    assert setup.status(F1) is PENDING


# 每只股票的待执行跟进上限


def test_stock_limit_boundary() -> None:
    """spec「上限的边界」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)

    assert setup.apply("CCC", OBSERVATION_REQUEST) is FollowUpRejection.STOCK_FULL
    assert isinstance(setup.apply("BBB", OBSERVATION_REQUEST), FollowUp)


def test_replacement_at_the_stock_limit() -> None:
    """spec「达到上限时仍可以替换」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)

    f3 = _approved(
        setup.apply("CCC", changed(SCHEDULE_REQUEST, trigger_at=at(14, 30, day=20, month=11)))
    )

    assert f3.follow_up_id == F3
    assert setup.status(F1) is REPLACED
    assert setup.pending_ids("CCC") == [F2, F3]


# 每天的申请次数


def test_daily_limit_boundary() -> None:
    """spec「次数上限的边界」：上限配置为 2。"""
    setup = _Setup(max_daily_requests=2)

    assert isinstance(setup.apply("CCC", SCHEDULE_REQUEST), FollowUp)
    assert isinstance(setup.apply("BBB", OBSERVATION_REQUEST), FollowUp)
    assert setup.apply("CCC", EVENT_REQUEST) is FollowUpRejection.DAILY_LIMIT


def test_replacement_counts_towards_the_daily_limit() -> None:
    """spec「替换也计入」：上限配置为 2。"""
    setup = _Setup(max_daily_requests=2)

    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", changed(SCHEDULE_REQUEST, trigger_at=at(14, 30, day=20, month=11)))

    assert setup.status(F1) is REPLACED
    assert setup.apply("BBB", OBSERVATION_REQUEST) is FollowUpRejection.DAILY_LIMIT


def test_rejected_requests_do_not_count() -> None:
    """spec「被拒绝的申请不计入」：上限配置为 1。"""
    setup = _Setup(max_daily_requests=1)

    no_reference = setup.apply("CCC", changed(EVENT_REQUEST, reference=None))
    passed = setup.apply("CCC", changed(EVENT_REQUEST, trigger_at=at(18, day=13)))

    assert (no_reference, passed) == (
        FollowUpRejection.NO_REFERENCE,
        FollowUpRejection.TRIGGER_PASSED,
    )
    assert isinstance(setup.apply("CCC", SCHEDULE_REQUEST), FollowUp)


def test_after_the_close_counts_for_the_next_session() -> None:
    """spec「收盘后的申请算下一个交易日」：上限配置为 1。"""
    setup = _Setup(at(19, 59), max_daily_requests=1)

    assert isinstance(setup.apply("CCC", SCHEDULE_REQUEST), FollowUp)
    setup.clock.set(at(20))  # 归属 2026-10-15
    assert isinstance(setup.apply("BBB", OBSERVATION_REQUEST), FollowUp)
    setup.clock.set(at(15, day=15))
    assert setup.apply("BBB", SCHEDULE_REQUEST) is FollowUpRejection.DAILY_LIMIT


def test_weekend_counts_for_the_next_session() -> None:
    """spec「周末并进下一个交易日」：上限配置为 1。"""
    setup = _Setup(at(15, day=17), max_daily_requests=1)  # 周六，归属 2026-10-19

    assert isinstance(setup.apply("CCC", SCHEDULE_REQUEST), FollowUp)
    setup.clock.set(at(15, day=19))
    assert setup.apply("BBB", OBSERVATION_REQUEST) is FollowUpRejection.DAILY_LIMIT
    setup.clock.set(at(20, day=19))  # 归属 2026-10-20
    assert isinstance(setup.apply("BBB", OBSERVATION_REQUEST), FollowUp)


# 跟进的配置


def test_config_changes_the_approval() -> None:
    """spec「改配置改变审批」。"""
    one_per_stock = _Setup(max_pending_per_stock=1)
    one_per_stock.approve("CCC", SCHEDULE_REQUEST)
    assert one_per_stock.apply("CCC", EVENT_REQUEST) is FollowUpRejection.STOCK_FULL

    twenty_sessions = _Setup(max_trigger_sessions=20)
    assert twenty_sessions.apply("CCC", SCHEDULE_REQUEST) is FollowUpRejection.TRIGGER_TOO_FAR
    request = changed(SCHEDULE_REQUEST, trigger_at=at(20, 59, day=11, month=11))
    assert isinstance(twenty_sessions.apply("CCC", request), FollowUp)


# 跟进的状态


def test_replaced_follow_up_is_not_triggered() -> None:
    """spec「被替换的跟进不再触发」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.clock.set(at(15, day=15))
    setup.approve("CCC", changed(SCHEDULE_REQUEST, trigger_at=at(14, 30, day=30, month=11)))

    assert setup.trigger_at(at(15, day=20, month=11)) == []
    assert (setup.status(F1), setup.status(F2)) == (REPLACED, PENDING)


def test_voided_follow_up_is_not_triggered() -> None:
    """spec「已作废的跟进不再触发」。"""
    setup = _Setup()
    setup.approve("CCC", EVENT_REQUEST)
    setup.service.status_changed("CCC", HOLDING, CANDIDATE)

    assert setup.trigger_at(at(18)) == []
    assert setup.status(F1) is VOIDED


def test_triggered_follow_up_does_not_change() -> None:
    """spec「已触发的跟进不再变化」。"""
    setup = _Setup()
    setup.approve("CCC", EVENT_REQUEST)
    assert setup.trigger_at(at(18)) == [F1]

    setup.clock.set(at(18, 30))
    f2 = _approved(setup.apply("CCC", changed(EVENT_REQUEST, trigger_at=at(15, day=15))))
    assert f2.follow_up_id == F2
    assert setup.status(F1) is TRIGGERED

    voided = setup.service.status_changed("CCC", HOLDING, CANDIDATE)
    assert [f.follow_up_id for f in voided] == [F2]
    assert setup.status(F1) is TRIGGERED


def test_follow_up_case_without_conclusion_is_not_triggered_again() -> None:
    """spec「跟进案件没有结论也不再触发」：开出的案件怎样结束都不传回本模块。"""
    setup = _Setup()
    setup.approve("CCC", EVENT_REQUEST)
    assert setup.trigger_at(at(18)) == [F1]

    assert setup.trigger_at(at(18, 30)) == []


# 每只股票的待执行跟进上限：触发以后


def test_triggering_frees_a_slot() -> None:
    """spec「触发以后腾出名额」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)
    assert setup.trigger_at(at(18)) == [F2]

    assert isinstance(setup.apply("CCC", OBSERVATION_REQUEST), FollowUp)


# 每天的申请次数：触发和作废以后


def test_triggering_does_not_refund_the_daily_count() -> None:
    """spec「触发和作废不退还次数」：到点触发。上限配置为 1。"""
    setup = _Setup(max_daily_requests=1)
    setup.approve("CCC", EVENT_REQUEST)
    assert setup.trigger_at(at(18)) == [F1]

    assert setup.apply("BBB", OBSERVATION_REQUEST) is FollowUpRejection.DAILY_LIMIT


def test_voiding_does_not_refund_the_daily_count() -> None:
    """spec「触发和作废不退还次数」：CCC 代码触发的清仓转为候选。上限配置为 1。"""
    setup = _Setup(max_daily_requests=1)
    setup.approve("CCC", EVENT_REQUEST)
    setup.service.status_changed("CCC", HOLDING, CANDIDATE)
    assert setup.status(F1) is VOIDED

    assert setup.apply("BBB", OBSERVATION_REQUEST) is FollowUpRejection.DAILY_LIMIT


# 到点触发


def test_trigger_boundary() -> None:
    """spec「到点的边界」。"""
    setup = _Setup()
    setup.approve("CCC", EVENT_REQUEST)

    assert setup.trigger_at(at(17, 59, 59)) == []
    assert setup.status(F1) is PENDING

    setup.clock.set(at(18))
    (f1,) = setup.service.trigger_due()
    assert (f1.follow_up_id, f1.symbol, f1.request.question) == (
        F1,
        "CCC",
        "大单公布 3 小时后盘中反应如何？",
    )
    assert f1.status is TRIGGERED
    assert setup.status(F1) is TRIGGERED
    assert setup.service.pending("CCC") == ()


def test_missed_follow_ups_are_given_together() -> None:
    """spec「错过以后一起给出」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)
    setup.approve("BBB", OBSERVATION_REQUEST)

    assert setup.trigger_at(at(15, day=20, month=11)) == [F2, F3, F1]
    assert all(setup.status(f) is TRIGGERED for f in (F1, F2, F3))


def test_same_trigger_time_in_approval_order() -> None:
    """spec「触发时间相同按获批的先后」。"""
    setup = _Setup()
    setup.approve("CCC", EVENT_REQUEST)
    setup.clock.set(at(16))
    setup.approve("BBB", EVENT_REQUEST)

    assert setup.trigger_at(at(18)) == [F1, F2]


def test_watch_follow_up_triggers_after_buying() -> None:
    """spec「关注时申请、买入以后照常触发」。开出的案件按持仓跟进运行由调度器的 define_case 决定，
    见 case-types spec「开始运行时确定案件类型」的测试。
    """
    setup = _Setup()
    setup.approve("BBB", OBSERVATION_REQUEST)
    assert setup.service.status_changed("BBB", WATCH, HOLDING) == ()

    setup.clock.set(at(20, day=21))
    (f1,) = setup.service.trigger_due()
    assert (f1.follow_up_id, f1.symbol, f1.request.question) == (
        F1,
        "BBB",
        "站上 55 以后能不能守住？",
    )


# 股票状态往下走时作废


@pytest.mark.parametrize(
    "after", [CANDIDATE, WATCH, None], ids=["代码触发的清仓", "PM 清仓转为关注", "PM 清仓移出"]
)
def test_closing_voids(after: StockStatus | None) -> None:
    """spec「清仓作废」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)
    setup.approve("BBB", OBSERVATION_REQUEST)

    voided = setup.service.status_changed("CCC", HOLDING, after)

    assert {f.follow_up_id for f in voided} == {F1, F2}
    assert all(f.status is VOIDED for f in voided)
    assert (setup.status(F1), setup.status(F2), setup.status(F3)) == (VOIDED, VOIDED, PENDING)


@pytest.mark.parametrize("after", [CANDIDATE, None], ids=["降为候选", "被删除"])
def test_watch_demoted_or_deleted_voids(after: StockStatus | None) -> None:
    """spec「关注降为候选或被删除时作废」。"""
    setup = _Setup()
    setup.approve("BBB", OBSERVATION_REQUEST)

    voided = setup.service.status_changed("BBB", WATCH, after)

    assert [f.follow_up_id for f in voided] == [F1]
    assert setup.status(F1) is VOIDED


def test_watch_bought_keeps() -> None:
    """spec「关注股买入以后保留」。"""
    setup = _Setup()
    setup.approve("BBB", OBSERVATION_REQUEST)

    assert setup.service.status_changed("BBB", WATCH, HOLDING) == ()
    assert setup.status(F1) is PENDING


@pytest.mark.parametrize(
    ("symbol", "before", "after"),
    [
        ("AAA", CANDIDATE, WATCH),
        ("AAA", CANDIDATE, HOLDING),
        ("AAA", CANDIDATE, None),
        ("DDD", None, CANDIDATE),
        ("CCC", HOLDING, HOLDING),
    ],
    ids=["AAA 升级", "AAA 买入成交", "AAA 被删除", "DDD 加入", "CCC 加仓成交"],
)
def test_candidate_changes_and_adding_keep(
    symbol: str, before: StockStatus | None, after: StockStatus | None
) -> None:
    """spec「候选的变化和加仓不作废」。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)

    assert setup.service.status_changed(symbol, before, after) == ()
    assert setup.status(F1) is PENDING


def test_request_again_after_voiding() -> None:
    """spec「作废以后可以重新申请」：传入 CCC 新的关注论点的催化剂（不引用）。"""
    setup = _Setup()
    setup.approve("CCC", SCHEDULE_REQUEST)
    setup.approve("CCC", EVENT_REQUEST)
    setup.service.status_changed("CCC", HOLDING, WATCH)
    assert (setup.status(F1), setup.status(F2)) == (VOIDED, VOIDED)
    watch_catalyst = Catalyst(description="新品发布", deadline=BBB_CATALYST.deadline)

    result = setup.apply("CCC", OBSERVATION_REQUEST, catalyst=watch_catalyst)

    assert isinstance(result, FollowUp)
    assert setup.pending_ids("CCC") == [F3]
