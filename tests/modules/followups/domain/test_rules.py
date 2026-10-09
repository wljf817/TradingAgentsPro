"""跟进审批规则的测试：审批的检查、触发时间的上限、原因相同、到点和作废，用纽交所样例日历。

场景见 openspec 的 follow-ups spec「审批的顺序」「引用的对象」「不和催化剂重复」「触发时间」
「原因相同时替换」「每只股票的待执行跟进上限」「每天的申请次数」「到点触发」「股票状态往下走时
作废」。基准数据见 tests/modules/followups/builders.py。
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from tests.modules.followups.builders import (
    BASELINE_NOW,
    BBB_CATALYST,
    CCC_CATALYST,
    E1,
    EVENT_E1,
    EVENT_REQUEST,
    F1,
    F2,
    F3,
    NYSE,
    OBSERVATION_REQUEST,
    PRICE_55,
    PRICE_56,
    S9,
    S10,
    SCHEDULE_REQUEST,
    V7,
    at,
    changed,
    follow_up,
)
from tradingagentspro.contracts.conclusion import FollowUpReason, FollowUpRequest
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import Catalyst, CatalystReference, Condition
from tradingagentspro.modules.followups import (
    FollowUp,
    FollowUpConfig,
    FollowUpRejection,
    due,
    request_rejection,
    same_reason,
    trigger_limit,
    voids_follow_ups,
)

CONFIG = FollowUpConfig()
EVENT, SCHEDULE, OBSERVATION = (
    FollowUpReason.EVENT_REACTION,
    FollowUpReason.EXTERNAL_SCHEDULE,
    FollowUpReason.OBSERVATION,
)
CANDIDATE, WATCH, HOLDING = StockStatus.CANDIDATE, StockStatus.WATCH, StockStatus.HOLDING
LIMIT = at(21, day=11, month=1, year=2027)  # 基准时刻的上限：2027-01-11（第 60 个交易日）收盘
PAST = at(15, day=13)  # 已经过去的触发时间

# CCC 的另一个待执行跟进：引用和原因都和日程申请不同
OTHER_PENDING = follow_up(OBSERVATION_REQUEST, follow_up_id=F2)


def _rejection(
    request: FollowUpRequest,
    *,
    catalyst: Catalyst = CCC_CATALYST,
    pending: Sequence[FollowUp] = (),
    approved_today: int = 0,
    now: datetime = BASELINE_NOW,
    config: FollowUpConfig = CONFIG,
) -> FollowUpRejection | None:
    """默认是 CCC 在基准时刻审批，还没有任何跟进，今天还没有获批的申请。"""
    return request_rejection(request, catalyst, pending, approved_today, now, NYSE, config)


# 审批的顺序，第 1 项：引用了具体对象


@pytest.mark.parametrize(
    "request_", [SCHEDULE_REQUEST, EVENT_REQUEST, OBSERVATION_REQUEST], ids=["日程", "事件", "观察"]
)
def test_no_reference_is_rejected(request_: FollowUpRequest) -> None:
    """spec「没有引用具体对象」。"""
    assert _rejection(changed(request_, reference=None)) is FollowUpRejection.NO_REFERENCE


# 第 2 项：原因类型和引用的对象对应


@pytest.mark.parametrize(
    "request_", [SCHEDULE_REQUEST, EVENT_REQUEST, OBSERVATION_REQUEST], ids=["日程", "事件", "观察"]
)
def test_matching_reference_passes(request_: FollowUpRequest) -> None:
    """spec「三种原因各自引用对应的对象」：事件后反应引用事件，外部日程引用日程项，价格或指标
    观察引用条件。
    """
    assert _rejection(request_) is None


@pytest.mark.parametrize(
    ("reason", "reference"),
    [
        (EVENT, S9),
        (SCHEDULE, V7),
        (SCHEDULE, PRICE_55),
        (OBSERVATION, V7),
        (EVENT, PRICE_55),
        (OBSERVATION, S9),
    ],
    ids=[
        "事件后反应引用日程项",
        "外部日程引用事件",
        "外部日程引用条件",
        "价格或指标观察引用事件",
        "事件后反应引用条件",
        "价格或指标观察引用日程项",
    ],
)
def test_mismatched_reference_is_rejected(
    reason: FollowUpReason, reference: CatalystReference | Condition
) -> None:
    """spec「原因和引用的对象不符」：spec 的四种，另外补上剩下的两种。"""
    request = changed(EVENT_REQUEST, reason=reason, reference=reference)

    assert _rejection(request) is FollowUpRejection.REFERENCE_MISMATCH


# 第 3 项：不和催化剂重复


def test_catalyst_schedule_item_is_covered() -> None:
    """spec「引用催化剂的日程项」。"""
    request = changed(SCHEDULE_REQUEST, reference=E1)

    assert _rejection(request) is FollowUpRejection.CATALYST_COVERED


def test_catalyst_event_is_covered() -> None:
    """spec「引用催化剂关联的事件」。"""
    catalyst = CCC_CATALYST.model_copy(update={"reference": V7})

    assert _rejection(EVENT_REQUEST, catalyst=catalyst) is FollowUpRejection.CATALYST_COVERED


@pytest.mark.parametrize(
    ("request_", "catalyst"),
    [
        (SCHEDULE_REQUEST, CCC_CATALYST),
        (changed(EVENT_REQUEST, reference=EVENT_E1), CCC_CATALYST),
        (changed(SCHEDULE_REQUEST, reference=E1), BBB_CATALYST),
        (OBSERVATION_REQUEST, CCC_CATALYST),
    ],
    ids=["日程项不同", "ID 相同、种类不同", "催化剂不引用", "引用条件"],
)
def test_different_objects_are_not_covered(request_: FollowUpRequest, catalyst: Catalyst) -> None:
    """spec「不同的对象不算重复」。"""
    assert _rejection(request_, catalyst=catalyst) is None


# 第 4、5 项：触发时间


@pytest.mark.parametrize("trigger_at", [BASELINE_NOW, at(18, day=13)], ids=["等于现在", "过去"])
def test_trigger_not_after_now_is_rejected(trigger_at: datetime) -> None:
    """spec「触发时间在现在之后」：不晚于现在。"""
    request = changed(EVENT_REQUEST, trigger_at=trigger_at)

    assert _rejection(request) is FollowUpRejection.TRIGGER_PASSED


def test_trigger_one_second_after_now_passes() -> None:
    """spec「触发时间在现在之后」：晚 1 秒。"""
    request = changed(EVENT_REQUEST, trigger_at=at(15, 0, 1))

    assert _rejection(request) is None


@pytest.mark.parametrize(
    "trigger_at",
    [LIMIT - timedelta(minutes=1), at(15, day=10, month=1, year=2027)],
    ids=["上限前 1 分钟", "周日，归属 2027-01-11"],
)
def test_trigger_before_the_limit_passes(trigger_at: datetime) -> None:
    """spec「最远间隔的边界」：通过。"""
    assert _rejection(changed(SCHEDULE_REQUEST, trigger_at=trigger_at)) is None


@pytest.mark.parametrize(
    "trigger_at",
    [LIMIT, at(14, 30, day=12, month=1, year=2027), at(15, day=1, month=1, year=2056)],
    ids=["正好在上限", "第 61 个交易日", "超出日历的覆盖范围"],
)
def test_trigger_at_or_after_the_limit_is_rejected(trigger_at: datetime) -> None:
    """spec「最远间隔的边界」：不通过。超出日历覆盖范围的时刻也只是被拒绝，不报错。"""
    request = changed(SCHEDULE_REQUEST, trigger_at=trigger_at)

    assert _rejection(request) is FollowUpRejection.TRIGGER_TOO_FAR


@pytest.mark.parametrize(
    ("now", "limit"),
    [
        (BASELINE_NOW, LIMIT),
        (at(20), at(21, day=12, month=1, year=2027)),  # 收盘时刻，归属 2026-10-15
        (at(19, 59, 59), LIMIT),  # 收盘前 1 秒，还是 2026-10-14
        (at(15, day=17), at(21, day=14, month=1, year=2027)),  # 周六，归属 2026-10-19
    ],
    ids=["盘中", "收盘时刻", "收盘前 1 秒", "周六"],
)
def test_trigger_limit(now: datetime, limit: datetime) -> None:
    """spec「最远间隔的边界」「收盘后审批从下一个交易日算起」的上限。"""
    assert trigger_limit(now, NYSE, CONFIG) == limit


@pytest.mark.parametrize(
    ("sessions", "limit"),
    [
        (1, at(20, day=15)),  # 夏令时，收盘 20:00Z
        (20, at(21, day=11, month=11)),  # 夏令时结束以后，收盘 21:00Z
    ],
)
def test_trigger_limit_follows_the_config(sessions: int, limit: datetime) -> None:
    """触发时间最远间隔改为 1 和 20；夏令时结束以后的收盘时刻是 21:00Z。"""
    config = FollowUpConfig(max_trigger_sessions=sessions)

    assert trigger_limit(BASELINE_NOW, NYSE, config) == limit


# 第 6 项：每只股票的待执行跟进上限


def test_stock_full_is_rejected() -> None:
    """spec「上限的边界」：已经有 2 个待执行的跟进。"""
    pending = [follow_up(EVENT_REQUEST), OTHER_PENDING]

    assert _rejection(SCHEDULE_REQUEST, pending=pending) is FollowUpRejection.STOCK_FULL


def test_below_the_stock_limit_passes() -> None:
    """spec「上限的边界」：只有 1 个待执行的跟进。"""
    assert _rejection(SCHEDULE_REQUEST, pending=[OTHER_PENDING]) is None


def test_replacement_is_not_limited_by_the_stock_limit() -> None:
    """spec「达到上限时仍可以替换」：有原因相同的待执行跟进时，不受每只股票的上限限制。"""
    pending = [follow_up(SCHEDULE_REQUEST), OTHER_PENDING]
    request = changed(SCHEDULE_REQUEST, trigger_at=at(14, 30, day=20, month=11))

    assert _rejection(request, pending=pending) is None


def test_stock_limit_follows_the_config() -> None:
    """spec「改配置改变审批」：上限配置为 1。"""
    config = FollowUpConfig(max_pending_per_stock=1)
    pending = [follow_up(SCHEDULE_REQUEST)]

    assert _rejection(EVENT_REQUEST, pending=pending, config=config) is FollowUpRejection.STOCK_FULL


# 第 7 项：每天的申请次数


@pytest.mark.parametrize(("approved_today", "passes"), [(4, True), (5, False)])
def test_daily_limit_boundary(approved_today: int, passes: bool) -> None:
    """spec「次数上限的边界」：今天已经获批 4 次和 5 次。"""
    result = _rejection(SCHEDULE_REQUEST, approved_today=approved_today)

    assert result is (None if passes else FollowUpRejection.DAILY_LIMIT)


def test_replacement_is_limited_by_the_daily_limit() -> None:
    """spec「替换也计入」：替换也要今天获批的申请少于上限。"""
    pending = [follow_up(SCHEDULE_REQUEST)]
    request = changed(SCHEDULE_REQUEST, trigger_at=at(14, 30, day=20, month=11))

    result = _rejection(request, pending=pending, approved_today=5)

    assert result is FollowUpRejection.DAILY_LIMIT


# 多项不通过时给出表里靠前的一项


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (changed(EVENT_REQUEST, reference=None, trigger_at=PAST), FollowUpRejection.NO_REFERENCE),
        (
            changed(EVENT_REQUEST, reference=E1, trigger_at=PAST),
            FollowUpRejection.REFERENCE_MISMATCH,
        ),
        (
            changed(SCHEDULE_REQUEST, reference=E1, trigger_at=PAST),
            FollowUpRejection.CATALYST_COVERED,
        ),
        (changed(OBSERVATION_REQUEST, trigger_at=PAST), FollowUpRejection.TRIGGER_PASSED),
        (changed(OBSERVATION_REQUEST, trigger_at=LIMIT), FollowUpRejection.TRIGGER_TOO_FAR),
        (OBSERVATION_REQUEST, FollowUpRejection.STOCK_FULL),
    ],
    ids=["没有引用", "原因和引用不符", "催化剂已经覆盖", "触发时间已过", "触发时间太远", "已满"],
)
def test_first_failing_check_is_given(
    request_: FollowUpRequest, expected: FollowUpRejection
) -> None:
    """spec「多项不通过时给出第一项」：每只股票的上限和每天的上限也同时不通过。"""
    pending = [follow_up(SCHEDULE_REQUEST), follow_up(EVENT_REQUEST, follow_up_id=F2)]

    assert _rejection(request_, pending=pending, approved_today=5) is expected


# 原因相同


def test_same_reason_found() -> None:
    """原因类型相同，并且引用同一个对象：给出那个待执行的跟进。"""
    pending = [OTHER_PENDING, follow_up(SCHEDULE_REQUEST)]
    request = changed(
        SCHEDULE_REQUEST, question="推迟以后呢？", trigger_at=at(15, day=30, month=11)
    )

    assert same_reason(request, pending) == follow_up(SCHEDULE_REQUEST)


@pytest.mark.parametrize(
    "request_",
    [
        changed(SCHEDULE_REQUEST, reference=S10),
        changed(SCHEDULE_REQUEST, reference=S9.model_copy(update={"kind": V7.kind})),
        changed(OBSERVATION_REQUEST, reference=PRICE_56),
        changed(EVENT_REQUEST, reference=S9),
    ],
    ids=["日程项不同", "ID 相同、种类不同", "条件的值不同", "原因类型不同"],
)
def test_same_reason_not_found(request_: FollowUpRequest) -> None:
    """spec「引用不同的对象不替换」：引用或原因类型有一项不同都不算原因相同。"""
    pending = [follow_up(SCHEDULE_REQUEST), OTHER_PENDING]

    assert same_reason(request_, pending) is None


def test_same_reason_without_pending() -> None:
    assert same_reason(SCHEDULE_REQUEST, []) is None


# 到点


def test_due_boundary() -> None:
    """spec「到点的边界」：触发时间等于现在的给出，晚 1 秒的不给。"""
    event = follow_up(EVENT_REQUEST)

    assert due([event], at(18)) == [event]
    assert due([event], at(17, 59, 59)) == []


def test_due_in_trigger_order() -> None:
    """spec「错过以后一起给出」「触发时间相同按获批的先后」：按触发时间，再按获批的先后。"""
    schedule = follow_up(SCHEDULE_REQUEST, follow_up_id=F1)
    early = follow_up(EVENT_REQUEST, follow_up_id=F2, requested_at=at(16))
    earlier = follow_up(EVENT_REQUEST, follow_up_id=F3, symbol="BBB", requested_at=BASELINE_NOW)
    observation = follow_up(OBSERVATION_REQUEST, follow_up_id=F3, symbol="BBB")
    not_yet = follow_up(changed(SCHEDULE_REQUEST, trigger_at=at(15, 0, 1, day=20, month=11)))

    assert due([schedule, early, earlier], at(18)) == [earlier, early]
    assert due([schedule, observation, early, not_yet], at(15, day=20, month=11)) == [
        early,
        observation,
        schedule,
    ]


# 作废


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (HOLDING, CANDIDATE),
        (HOLDING, WATCH),
        (HOLDING, None),
        (WATCH, CANDIDATE),
        (WATCH, None),
        (CANDIDATE, None),
    ],
    ids=[
        "代码触发的清仓",
        "PM 发起的清仓，转为关注",
        "PM 发起的清仓，移出",
        "降为候选",
        "关注被删除",
        "候选被删除或超期移出",
    ],
)
def test_lower_status_voids(before: StockStatus, after: StockStatus | None) -> None:
    """spec「股票状态往下走时作废」表里变到更低状态的几行。候选没有待执行的跟进，作废的是空的。"""
    assert voids_follow_ups(before, after)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (WATCH, HOLDING),
        (CANDIDATE, WATCH),
        (CANDIDATE, HOLDING),
        (None, CANDIDATE),
        (HOLDING, HOLDING),
    ],
    ids=["关注股买入成交", "候选升级", "候选买入成交", "加入", "持仓加仓成交"],
)
def test_other_changes_keep(before: StockStatus | None, after: StockStatus | None) -> None:
    """spec「股票状态往下走时作废」表的其他行，以及之前和之后相同（加仓成交）。"""
    assert not voids_follow_ups(before, after)
