"""论点里日期的检查的测试：复查日、催化剂和上次复查的那一天，用纽交所样例日历。

场景见 openspec 的 review-dates spec。基准数据见 tests/modules/heartbeat/builders.py：今天
2026-10-14（周三），原来的论点是基准论点，上次复查的那一天 2026-10-07，持仓的浮动盈亏比例 0.02。
没有写明论点种类的场景，关注论点和投资论点各跑一遍。
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from tests.modules.heartbeat.builders import (
    BASELINES,
    E1,
    E1_DAY,
    E2,
    E2_DAY,
    INVESTMENT_THESIS,
    KINDS,
    LAST_REVIEW_DAY,
    NEW_CATALYST,
    NYSE,
    TODAY,
    V7,
    WATCH_THESIS,
    Thesis,
    adjustment,
    catalyst_due,
    deadline_on,
    new,
    on,
    referencing,
    renewal,
    review_due,
    review_on,
    with_catalyst,
)
from tradingagentspro.modules.heartbeat import (
    HeartbeatConfig,
    ThesisDatesRejection,
    ThesisUpdate,
    last_review_day_after,
    thesis_dates_rejection,
)

CONFIG = HeartbeatConfig()
REVIEW_TOO_SOON = ThesisDatesRejection.REVIEW_TOO_SOON
POSTPONED_IN_LOSS = ThesisDatesRejection.POSTPONED_IN_LOSS
REVIEW_TOO_LATE = ThesisDatesRejection.REVIEW_TOO_LATE
REVIEW_NOT_SESSION = ThesisDatesRejection.REVIEW_NOT_SESSION
STALE_CATALYST = ThesisDatesRejection.STALE_CATALYST
DEADLINE_TOO_SOON = ThesisDatesRejection.DEADLINE_TOO_SOON
UNKNOWN_SCHEDULE = ThesisDatesRejection.UNKNOWN_SCHEDULE
OFF_SCHEDULE = ThesisDatesRejection.OFF_SCHEDULE
DEADLINE_TOO_LATE = ThesisDatesRejection.DEADLINE_TOO_LATE
DEADLINE_NOT_SESSION = ThesisDatesRejection.DEADLINE_NOT_SESSION
LOSS = Decimal("-0.10")

kinds = pytest.mark.parametrize("baseline", BASELINES, ids=KINDS)


def _check(
    update: ThesisUpdate, today: date = TODAY, config: HeartbeatConfig = CONFIG
) -> ThesisDatesRejection | None:
    """基准数据下检查论点里的日期；参数覆盖今天和配置。"""
    return thesis_dates_rejection(update, today, NYSE, config)


# 检查论点里的日期


@kinds
def test_baselines_pass_in_every_situation(baseline: Thesis) -> None:
    """spec「基准论点在三种情形下都通过」：新设基准论点，续期和平时调整写回原来的论点。"""
    assert _check(new(baseline)) is None
    assert _check(renewal(baseline, baseline)) is None
    assert _check(adjustment(baseline, baseline)) is None


@pytest.mark.parametrize(
    "call",
    [
        lambda: _check(new(INVESTMENT_THESIS), today=on(10, 17)),
        lambda: ThesisUpdate(INVESTMENT_THESIS, renewal=True),
        lambda: ThesisUpdate(INVESTMENT_THESIS, previous=INVESTMENT_THESIS),
        lambda: ThesisUpdate(INVESTMENT_THESIS, last_review_day=LAST_REVIEW_DAY),
        lambda: ThesisUpdate(
            WATCH_THESIS, previous=INVESTMENT_THESIS, last_review_day=LAST_REVIEW_DAY
        ),
    ],
    ids=[
        "今天是周六",
        "续期不传原来的论点",
        "平时调整没传上次复查的那一天",
        "新设传了上次复查的那一天",
        "新论点是关注论点、原来的是投资论点",
    ],
)
def test_invalid_input_is_an_error(call: Callable[[], object]) -> None:
    """spec「不合法的输入」：都报错。"""
    with pytest.raises(ValueError):
        call()


# 检查的顺序


@kinds
def test_the_first_failing_check_is_given(baseline: Thesis) -> None:
    """spec「多项不通过时给出第一项」。"""
    too_soon_both = deadline_on(review_on(baseline, on(10, 16)), TODAY)
    weekend_and_unknown_schedule = referencing(review_on(baseline, on(10, 24)), E1)
    later_in_loss = review_on(INVESTMENT_THESIS, on(11, 5))
    previous = catalyst_due(baseline)
    weekend_and_stale = deadline_on(previous, on(10, 17))

    assert _check(new(too_soon_both)) is REVIEW_TOO_SOON
    assert _check(new(weekend_and_unknown_schedule)) is REVIEW_NOT_SESSION
    assert _check(adjustment(later_in_loss, INVESTMENT_THESIS, pnl_ratio=LOSS)) is POSTPONED_IN_LOSS
    assert _check(renewal(weekend_and_stale, previous)) is STALE_CATALYST


@kinds
def test_range_is_compared_before_sessions(baseline: Thesis) -> None:
    """spec「先比较范围再查交易日」：2027-01-16 是周六，超出范围给出"太远"。"""
    far_weekend = on(1, 16, 2027)

    assert _check(new(review_on(baseline, far_weekend))) is REVIEW_TOO_LATE
    assert _check(new(deadline_on(baseline, far_weekend))) is DEADLINE_TOO_LATE


# 新设和续期的复查日


@kinds
@pytest.mark.parametrize(
    ("review_day", "rejection"),
    [
        (on(10, 16), REVIEW_TOO_SOON),
        (on(10, 19), None),
        (on(11, 11), None),
        (on(11, 12), REVIEW_TOO_LATE),
    ],
    ids=str,
)
def test_new_review_day_boundaries(
    baseline: Thesis, review_day: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「新设的边界」：今天之后第 3 个交易日是 10-19，第 20 个是 11-11。"""
    assert _check(new(review_on(baseline, review_day))) is rejection


@kinds
@pytest.mark.parametrize(
    ("review_day", "rejection"),
    [
        (on(10, 13), REVIEW_TOO_SOON),
        (on(10, 16), REVIEW_TOO_SOON),
        (on(10, 19), None),
        (on(11, 11), None),
        (on(11, 12), REVIEW_TOO_LATE),
    ],
    ids=str,
)
def test_renewal_review_day_boundaries(
    baseline: Thesis, review_day: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「续期的边界」：原来的论点是复查日到期的原来论点，写回 10-13 也太近。"""
    previous = review_due(baseline)

    assert _check(renewal(review_on(previous, review_day), previous)) is rejection


@kinds
def test_renewal_may_keep_a_review_day_not_yet_due(baseline: Thesis) -> None:
    """spec「续期可以写回还没到的复查日」：催化剂复查，复查日写回 10-21，换成新的催化剂。"""
    previous = catalyst_due(baseline)

    assert _check(renewal(with_catalyst(previous, NEW_CATALYST), previous)) is None


@kinds
def test_new_and_renewal_review_days_must_be_sessions(baseline: Thesis) -> None:
    """spec「新设和续期的复查日不是交易日」：周末在范围内给出"不是交易日"，在范围外给出"太近"。"""
    previous = review_due(baseline)

    assert _check(new(review_on(baseline, on(10, 24)))) is REVIEW_NOT_SESSION
    assert _check(new(review_on(baseline, on(11, 1)))) is REVIEW_NOT_SESSION
    assert _check(renewal(review_on(previous, on(10, 31)), previous)) is REVIEW_NOT_SESSION
    assert _check(new(review_on(baseline, on(10, 17)))) is REVIEW_TOO_SOON


# 平时调整的复查日


@kinds
def test_unchanged_review_day_is_not_checked(baseline: Thesis) -> None:
    """spec「原样保留不检查」：包括已经到期的复查日 10-13。"""
    previous = review_due(baseline)

    assert _check(adjustment(baseline, baseline)) is None
    assert _check(adjustment(previous, previous)) is None


@kinds
@pytest.mark.parametrize(
    ("review_day", "rejection"), [(on(10, 15), None), (on(10, 14), REVIEW_TOO_SOON)], ids=str
)
def test_moving_earlier_ignores_the_minimum_interval(
    baseline: Thesis, review_day: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「提前不受最短间隔限制」：最早是下一个交易日 10-15。"""
    assert _check(adjustment(review_on(baseline, review_day), baseline)) is rejection


@kinds
@pytest.mark.parametrize(
    ("review_day", "rejection"), [(on(11, 4), None), (on(11, 5), REVIEW_TOO_LATE)], ids=str
)
def test_postponing_is_limited_from_the_last_review(
    baseline: Thesis, review_day: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「推后的上限从上次复查那天算」：10-07 之后第 20 个交易日是 11-04；11-05 是今天之后
    第 16 个交易日，但在 10-07 之后第 21 个。
    """
    assert _check(adjustment(review_on(baseline, review_day), baseline)) is rejection


@kinds
@pytest.mark.parametrize(
    ("review_day", "rejection"),
    [(on(10, 14), REVIEW_TOO_SOON), (on(11, 4), None), (on(11, 5), REVIEW_TOO_LATE)],
    ids=str,
)
def test_postponing_a_due_review_day(
    baseline: Thesis, review_day: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「到期的复查日推后」：原来的论点是复查日到期的原来论点。"""
    previous = review_due(baseline)

    assert _check(adjustment(review_on(previous, review_day), previous)) is rejection


@kinds
@pytest.mark.parametrize("review_day", [on(10, 17), on(10, 24)], ids=["提前到周六", "推后到周六"])
def test_adjusted_review_day_must_be_a_session(baseline: Thesis, review_day: date) -> None:
    """spec「平时调整的复查日不是交易日」。"""
    assert _check(adjustment(review_on(baseline, review_day), baseline)) is REVIEW_NOT_SESSION


# 亏损时不能推后复查日


@pytest.mark.parametrize(
    ("pnl_ratio", "rejection"),
    [(Decimal("-0.05"), None), (Decimal("-0.0501"), POSTPONED_IN_LOSS), (None, POSTPONED_IN_LOSS)],
    ids=["-0.05", "-0.0501", "查不到"],
)
def test_loss_boundary(pnl_ratio: Decimal | None, rejection: ThesisDatesRejection | None) -> None:
    """spec「亏损幅度的边界」：投资论点的平时调整，复查日推后到 10-22。"""
    later = review_on(INVESTMENT_THESIS, on(10, 22))

    assert _check(adjustment(later, INVESTMENT_THESIS, pnl_ratio=pnl_ratio)) is rejection


def test_in_loss_review_day_may_move_earlier_or_stay() -> None:
    """spec「亏损时可以提前和原样保留」：浮动盈亏比例 -0.10。"""
    earlier = review_on(INVESTMENT_THESIS, on(10, 15))

    assert _check(adjustment(earlier, INVESTMENT_THESIS, pnl_ratio=LOSS)) is None
    assert _check(adjustment(INVESTMENT_THESIS, INVESTMENT_THESIS, pnl_ratio=LOSS)) is None


def test_watch_theses_ignore_losses() -> None:
    """spec「关注论点不看亏损」：复查日推后到 10-22，不传浮动盈亏比例。"""
    later = review_on(WATCH_THESIS, on(10, 22))

    assert _check(adjustment(later, WATCH_THESIS, pnl_ratio=None)) is None


@pytest.mark.parametrize(
    ("review_day", "pnl_ratio", "rejection"),
    [
        (on(10, 19), LOSS, None),
        (on(10, 21), LOSS, None),
        (on(10, 22), LOSS, POSTPONED_IN_LOSS),
        (on(11, 11), LOSS, POSTPONED_IN_LOSS),
        (on(10, 16), LOSS, REVIEW_TOO_SOON),
        (on(11, 11), Decimal("-0.05"), None),
    ],
    ids=str,
)
def test_renewal_in_loss_reviews_at_the_default_interval(
    review_day: date, pnl_ratio: Decimal, rejection: ThesisDatesRejection | None
) -> None:
    """spec「亏损时续期按默认间隔复查」：投资论点的续期，原来的论点是复查日到期的原来论点；
    今天之后第 5 个交易日是 10-21。
    """
    previous = review_due(INVESTMENT_THESIS)
    update = renewal(review_on(previous, review_day), previous, pnl_ratio=pnl_ratio)

    assert _check(update) is rejection


# 催化剂到期以后


@kinds
def test_renewal_needs_a_new_catalyst_after_the_old_one_is_due(baseline: Thesis) -> None:
    """spec「续期时到期的催化剂必须换新的」：原来的论点是催化剂到期的原来论点。"""
    previous = catalyst_due(baseline)
    same_description_on_e1 = deadline_on(referencing(previous, E1), on(10, 29))

    assert _check(renewal(previous, previous)) is STALE_CATALYST
    assert _check(renewal(deadline_on(previous, on(11, 20)), previous)) is STALE_CATALYST
    assert _check(renewal(referencing(previous, V7), previous)) is STALE_CATALYST
    assert _check(renewal(with_catalyst(previous, NEW_CATALYST), previous)) is None
    assert _check(renewal(same_description_on_e1, previous, schedule_day=E1_DAY)) is None


@kinds
def test_a_catalyst_due_today_is_due(baseline: Thesis) -> None:
    """spec「到期当天也算到期」：续期，催化剂原样保留。"""
    due_today = deadline_on(baseline, TODAY)
    due_tomorrow = deadline_on(baseline, on(10, 15))

    assert _check(renewal(due_today, due_today)) is STALE_CATALYST
    assert _check(renewal(due_tomorrow, due_tomorrow)) is None


@kinds
def test_renewal_may_keep_a_catalyst_not_yet_due(baseline: Thesis) -> None:
    """spec「续期时没到期的催化剂可以保留」：原来的论点是复查日到期的原来论点，复查日改为 10-21。"""
    previous = review_due(baseline)
    renewed = review_on(previous, on(10, 21))

    assert _check(renewal(renewed, previous)) is None
    assert _check(renewal(referencing(renewed, V7), previous)) is None


@kinds
def test_adjustment_after_the_catalyst_is_due(baseline: Thesis) -> None:
    """spec「平时调整时到期的催化剂」：原样保留、只关联事件都可以，改了就必须是新的。"""
    previous = catalyst_due(baseline)

    assert _check(adjustment(previous, previous)) is None
    assert _check(adjustment(referencing(previous, V7), previous)) is None
    assert _check(adjustment(deadline_on(previous, on(11, 20)), previous)) is STALE_CATALYST
    assert _check(adjustment(with_catalyst(previous, NEW_CATALYST), previous)) is None


@kinds
def test_adjustment_may_change_a_catalyst_not_yet_due(baseline: Thesis) -> None:
    """spec「平时调整时没到期的催化剂可以改」：改了的按截止日期检查。"""
    assert _check(adjustment(referencing(baseline, V7), baseline)) is None
    assert _check(adjustment(deadline_on(baseline, on(11, 20)), baseline)) is None
    assert _check(adjustment(deadline_on(baseline, on(10, 22)), baseline)) is None
    assert _check(adjustment(deadline_on(baseline, TODAY), baseline)) is DEADLINE_TOO_SOON


# 催化剂的截止日期


@kinds
@pytest.mark.parametrize(
    ("deadline", "rejection"),
    [(on(10, 14), DEADLINE_TOO_SOON), (on(10, 13), DEADLINE_TOO_SOON), (on(10, 15), None)],
    ids=str,
)
def test_deadline_is_after_today(
    baseline: Thesis, deadline: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「截止日期在今天之后」。"""
    assert _check(new(deadline_on(baseline, deadline))) is rejection


@kinds
@pytest.mark.parametrize(
    ("deadline", "rejection"),
    [
        (on(10, 29), None),
        (on(10, 30), None),
        (on(10, 28), OFF_SCHEDULE),
        (on(11, 2), OFF_SCHEDULE),
    ],
    ids=str,
)
def test_deadline_follows_the_schedule_item(
    baseline: Thesis, deadline: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「引用日程项的截止日期」：E-1 是 10-28，之后第 1、2 个交易日是 10-29、10-30。"""
    thesis = deadline_on(referencing(baseline, E1), deadline)

    assert _check(new(thesis, schedule_day=E1_DAY)) is rejection


@kinds
@pytest.mark.parametrize(
    ("deadline", "rejection"),
    [(on(10, 23), None), (on(10, 26), None), (on(10, 24), DEADLINE_NOT_SESSION)],
    ids=str,
)
def test_schedule_item_before_a_weekend(
    baseline: Thesis, deadline: date, rejection: ThesisDatesRejection | None
) -> None:
    """spec「日程项之后跨周末」：E-2 是 10-22（周四），之后第 1、2 个交易日是 10-23（周五）、
    10-26（周一）。
    """
    thesis = deadline_on(referencing(baseline, E2), deadline)

    assert _check(new(thesis, schedule_day=E2_DAY)) is rejection


@kinds
def test_unknown_schedule_item(baseline: Thesis) -> None:
    """spec「查不到日程项」：引用 E-1，调用方没有传日程项的日期。"""
    assert _check(new(referencing(baseline, E1))) is UNKNOWN_SCHEDULE


@kinds
def test_deadline_limit_without_a_schedule_item(baseline: Thesis) -> None:
    """spec「不引用日程项的截止日期上限」：今天之后第 60 个交易日是 2027-01-11，引用事件的
    也一样。
    """
    at_limit = deadline_on(baseline, on(1, 11, 2027))

    assert _check(new(at_limit)) is None
    assert _check(new(referencing(at_limit, V7))) is None
    assert _check(new(deadline_on(baseline, on(1, 12, 2027)))) is DEADLINE_TOO_LATE


@kinds
@pytest.mark.parametrize("deadline", [on(10, 17), on(11, 26)], ids=["周六", "感恩节"])
def test_deadline_must_be_a_session(baseline: Thesis, deadline: date) -> None:
    """spec「截止日期不是交易日」。"""
    assert _check(new(deadline_on(baseline, deadline))) is DEADLINE_NOT_SESSION


@kinds
def test_unchanged_catalyst_is_not_checked_again(baseline: Thesis) -> None:
    """spec「原样保留的催化剂不再检查」：原来的催化剂引用 E-1、截止日期 10-29，平时调整原样
    保留，没有传日程项的日期。
    """
    previous = referencing(baseline, E1)

    assert _check(adjustment(previous, previous)) is None


# 上次复查的那一天


@kinds
def test_new_and_renewal_count_from_today(baseline: Thesis) -> None:
    """spec「新设和续期从今天算起」：续期的原来论点是复查日到期的原来论点，复查日改为 10-19。"""
    previous = review_due(baseline)
    renewed = renewal(review_on(previous, on(10, 19)), previous)

    assert _check(renewed) is None
    assert last_review_day_after(new(baseline), TODAY) == TODAY
    assert last_review_day_after(renewed, TODAY) == TODAY


@kinds
def test_adjustment_keeps_the_last_review_day(baseline: Thesis) -> None:
    """spec「平时调整不变」：复查日改为 10-15；持仓事件里决定加仓，论点更新是原来的论点。"""
    earlier = adjustment(review_on(baseline, on(10, 15)), baseline)
    add_in_holding_event = adjustment(INVESTMENT_THESIS, INVESTMENT_THESIS)

    assert last_review_day_after(earlier, TODAY) == LAST_REVIEW_DAY
    assert last_review_day_after(add_in_holding_event, TODAY) == LAST_REVIEW_DAY


@kinds
def test_postponing_limit_moves_with_a_renewal(baseline: Thesis) -> None:
    """spec「续期以后推后的上限跟着变」：10-14 续期，复查日 10-19；10-15 平时调整推后，上限是
    10-14 之后第 20 个交易日 11-11，不是今天之后第 20 个交易日 11-12。
    """
    previous = review_due(baseline)
    renewed = review_on(previous, on(10, 19))
    last_review_day = last_review_day_after(renewal(renewed, previous), TODAY)

    def postponed(day: date) -> ThesisUpdate:
        return adjustment(review_on(renewed, day), renewed, last_review_day=last_review_day)

    assert last_review_day == TODAY
    assert _check(postponed(on(11, 11)), today=on(10, 15)) is None
    assert _check(postponed(on(11, 12)), today=on(10, 15)) is REVIEW_TOO_LATE


# 复查日与催化剂的配置


@kinds
def test_config_changes_the_checks(baseline: Thesis) -> None:
    """spec「改配置改变检查」。"""
    shorter_reviews = HeartbeatConfig(max_review_sessions=10)
    looser_loss = HeartbeatConfig(max_postpone_loss=Decimal("0.10"))
    shorter_deadlines = HeartbeatConfig(max_schedule_lag_sessions=1, max_deadline_sessions=30)
    postponed = adjustment(review_on(baseline, on(10, 22)), baseline)
    postponed_in_loss = adjustment(
        review_on(INVESTMENT_THESIS, on(10, 22)), INVESTMENT_THESIS, pnl_ratio=Decimal("-0.06")
    )
    second_day_after_e1 = deadline_on(referencing(baseline, E1), on(10, 30))

    # 复查日最长间隔 10：今天之后第 10 个交易日是 10-28，10-07 之后第 10 个是 10-21
    assert _check(new(review_on(baseline, on(10, 28))), config=shorter_reviews) is None
    assert _check(new(review_on(baseline, on(10, 29))), config=shorter_reviews) is REVIEW_TOO_LATE
    assert _check(postponed, config=shorter_reviews) is REVIEW_TOO_LATE
    # 不能推后的亏损幅度 0.10
    assert _check(postponed_in_loss, config=looser_loss) is None
    # 日程项后的截止期限 1，催化剂截止日期最长间隔 30：今天之后第 30 个交易日是 11-25
    assert (
        _check(new(second_day_after_e1, schedule_day=E1_DAY), config=shorter_deadlines)
        is OFF_SCHEDULE
    )
    assert _check(new(deadline_on(baseline, on(11, 25))), config=shorter_deadlines) is None
    assert (
        _check(new(deadline_on(baseline, on(11, 27))), config=shorter_deadlines)
        is DEADLINE_TOO_LATE
    )
