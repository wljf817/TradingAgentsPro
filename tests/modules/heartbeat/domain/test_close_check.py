"""收盘检查的测试，用纽交所样例日历。

场景见 openspec 的 close-check spec，每个场景一个测试。基准数据见 tests/modules/heartbeat/
builders.py：今天 2026-10-14（周三）；持仓 CCC、EEE，关注 BBB，复查日都是 10-21，催化剂截止日期
都是 10-29；候选 AAA 评分 1、DDD 评分 2、FFF、GGG、HHH 评分 0，过期时间都是 2026-10-27 20:00Z，
都不是新候选；还没有寻股得出过结论；没有在排队或运行的心跳案件。
"""

from dataclasses import replace
from datetime import date

import pytest

from tests.modules.heartbeat.builders import (
    EXPLORATION,
    NYSE,
    SNAPSHOT,
    WEEKLY_RETROSPECTIVE,
    at_close,
    candidate,
    deadlines,
    evaluation,
    expiries,
    holding_catalyst_review,
    holding_review,
    in_flight,
    new_candidates,
    on,
    review_days,
    scores,
    watch_catalyst_review,
    watch_review,
)
from tradingagentspro.modules.heartbeat import (
    CheckItem,
    CloseSnapshot,
    HeartbeatConfig,
    PoolCandidate,
    close_check,
)

CONFIG = HeartbeatConfig()


def _check(snapshot: CloseSnapshot, config: HeartbeatConfig = CONFIG) -> tuple[CheckItem, ...]:
    """用样例日历做收盘检查；默认配置。"""
    return close_check(snapshot, NYSE, config)


# 收盘检查的输入和输出


def test_baseline_opens_nothing() -> None:
    """spec「基准数据不开」。"""
    assert _check(SNAPSHOT) == ()


@pytest.mark.parametrize("today", [on(10, 17), on(11, 26)], ids=["周六", "感恩节"])
def test_today_must_be_a_session(today: date) -> None:
    """spec「今天不是交易日」：都报错。"""
    with pytest.raises(ValueError):
        _check(replace(SNAPSHOT, today=today))


# 复查日和催化剂截止日期到期


def test_review_day_boundaries() -> None:
    """spec「复查日到期的边界」：今天和今天以前到期，明天还没到期。"""
    assert _check(review_days(SNAPSHOT, CCC=on(10, 14))) == (holding_review("CCC"),)
    assert _check(review_days(SNAPSHOT, CCC=on(10, 13))) == (holding_review("CCC"),)
    assert _check(review_days(SNAPSHOT, CCC=on(10, 15))) == ()


def test_catalyst_deadline_boundaries() -> None:
    """spec「催化剂截止日期到期的边界」。"""
    assert _check(deadlines(SNAPSHOT, CCC=on(10, 14))) == (holding_catalyst_review("CCC"),)
    assert _check(deadlines(SNAPSHOT, CCC=on(10, 9))) == (holding_catalyst_review("CCC"),)
    assert _check(deadlines(SNAPSHOT, CCC=on(10, 15))) == ()


def test_watch_dates_due() -> None:
    """spec「关注股到期」。"""
    assert _check(review_days(SNAPSHOT, BBB=on(10, 14))) == (watch_review("BBB"),)
    assert _check(deadlines(SNAPSHOT, BBB=on(10, 14))) == (watch_catalyst_review("BBB"),)


def test_both_due_opens_only_the_catalyst_review() -> None:
    """spec「两项都到期只开催化剂复查」。"""
    both_today = deadlines(review_days(SNAPSHOT, CCC=on(10, 14)), CCC=on(10, 14))
    review_earlier = deadlines(review_days(SNAPSHOT, BBB=on(10, 13)), BBB=on(10, 14))

    assert _check(both_today) == (holding_catalyst_review("CCC"),)
    assert _check(review_earlier) == (watch_catalyst_review("BBB"),)


def test_several_stocks_due_together() -> None:
    """spec「几只股票同时到期」：持仓在前、按股票代码，关注在后。"""
    snapshot = review_days(deadlines(SNAPSHOT, EEE=on(10, 14)), CCC=on(10, 14), BBB=on(10, 14))

    assert _check(snapshot) == (
        holding_review("CCC"),
        holding_catalyst_review("EEE"),
        watch_review("BBB"),
    )


# 在排队或运行的不重复开


def test_same_item_still_queued() -> None:
    """spec「同一事项还在排队」：10-13 收盘开出的 CCC 的持仓复查还在排队。"""
    snapshot = in_flight(review_days(SNAPSHOT, CCC=on(10, 13)), holding_review("CCC"))

    assert _check(snapshot) == ()


def test_another_review_in_flight_blocks() -> None:
    """spec「别的复查类案件在排队或运行」：CCC 的持仓复查在排队，BBB 的关注催化剂复查在运行。

    排队的和运行中的在快照里一样，都是在排队或运行的检查项。
    """
    holding = in_flight(deadlines(SNAPSHOT, CCC=on(10, 14)), holding_review("CCC"))
    watch = in_flight(review_days(SNAPSHOT, BBB=on(10, 14)), watch_catalyst_review("BBB"))

    assert _check(holding) == ()
    assert _check(watch) == ()


def test_only_that_stock_is_blocked() -> None:
    """spec「只挡这只股票」。"""
    snapshot = in_flight(
        review_days(SNAPSHOT, CCC=on(10, 13), EEE=on(10, 14)), holding_review("CCC")
    )

    assert _check(snapshot) == (holding_review("EEE"),)


def test_cases_not_opened_by_the_heartbeat_do_not_block() -> None:
    """spec「不是心跳开出的案件不挡」：CCC 的持仓事件和跟进不在快照里。"""
    assert _check(review_days(SNAPSHOT, CCC=on(10, 14))) == (holding_review("CCC"),)


# 没有结论时顺延


def test_item_opened_while_degraded_stays_queued() -> None:
    """spec「降级期间开出的留在队列里」：收盘检查不看降级模式，第二天在排队的不再开。"""
    due = review_days(SNAPSHOT, CCC=on(10, 13))

    assert _check(replace(due, today=on(10, 13))) == (holding_review("CCC"),)
    assert _check(in_flight(due, holding_review("CCC"))) == ()


def test_item_reopens_after_ending_without_a_conclusion() -> None:
    """spec「没有结论就结束以后再开」：系统故障、结论无效以后日期没变，也不在排队或运行。"""
    assert _check(review_days(SNAPSHOT, CCC=on(10, 13))) == (holding_review("CCC"),)
    assert _check(deadlines(SNAPSHOT, BBB=on(10, 13))) == (watch_catalyst_review("BBB"),)


def test_renewed_item_is_no_longer_due() -> None:
    """spec「续期以后不再到期」：CCC 的复查日续期为 10-19。"""
    assert _check(review_days(SNAPSHOT, CCC=on(10, 19))) == ()


def test_item_reopens_when_a_non_renewal_is_not_carried_out() -> None:
    """spec「不续期的结论没有执行成」：清仓被人工拒绝、开仓方案确认超时，状态和日期都没变。"""
    assert _check(review_days(SNAPSHOT, CCC=on(10, 13))) == (holding_review("CCC"),)
    assert _check(review_days(SNAPSHOT, BBB=on(10, 13))) == (watch_review("BBB"),)


# 候选评估


def test_evaluations_by_score_up_to_the_limit() -> None:
    """spec「按评分从高到低开到上限」：FFF 和 GGG 评分、过期时间都相同，按股票代码。"""
    snapshot = new_candidates(SNAPSHOT, "AAA", "DDD", "FFF", "GGG")

    assert _check(snapshot) == (evaluation("DDD"), evaluation("AAA"), evaluation("FFF"))


def test_unselected_candidate_waits_for_the_next_session() -> None:
    """spec「选不上的留到下一个交易日」：DDD、AAA、FFF 得出结论，不再是新候选。"""
    snapshot = replace(new_candidates(SNAPSHOT, "GGG"), today=on(10, 15))

    assert _check(snapshot) == (evaluation("GGG"),)


def test_earlier_expiry_first_on_equal_scores() -> None:
    """spec「评分相同时先过期的先开」。"""
    snapshot = expiries(
        new_candidates(SNAPSHOT, "AAA", "DDD", "FFF", "GGG"), at_close(10, 26), "GGG"
    )

    assert _check(snapshot) == (evaluation("DDD"), evaluation("AAA"), evaluation("GGG"))


def test_candidates_without_a_score_come_last() -> None:
    """spec「没有评分的排在最后」。"""
    snapshot = new_candidates(scores(SNAPSHOT, FFF=None), "AAA", "FFF", "GGG", "HHH")

    assert _check(snapshot) == (evaluation("AAA"), evaluation("GGG"), evaluation("HHH"))


def test_in_flight_evaluations_count_toward_the_limit() -> None:
    """spec「在排队或运行的占上限」。"""
    new = new_candidates(SNAPSHOT, "AAA", "DDD", "FFF", "GGG")
    one_queued = in_flight(new, evaluation("AAA"))
    limit_reached = in_flight(new, evaluation("AAA"), evaluation("DDD"), evaluation("FFF"))

    assert _check(one_queued) == (evaluation("DDD"), evaluation("FFF"))
    assert _check(limit_reached) == ()


def test_evaluation_reopens_after_ending_without_a_conclusion() -> None:
    """spec「没有结论的候选评估再开」：AAA 仍是新候选，不在排队或运行。"""
    assert _check(new_candidates(SNAPSHOT, "AAA")) == (evaluation("AAA"),)


def test_code_triggered_close_out_is_evaluated_the_same_day() -> None:
    """spec「代码触发的清仓当天开候选评估」：CCC 止损单成交、转为候选，和其他新候选一起排。"""
    ccc = PoolCandidate("CCC", 1, at_close(10, 28), new=True)
    closed_out = replace(
        SNAPSHOT,
        holdings=tuple(s for s in SNAPSHOT.holdings if s.symbol != "CCC"),
        candidates=(*SNAPSHOT.candidates, ccc),
    )

    assert _check(closed_out) == (evaluation("CCC"),)
    assert _check(new_candidates(closed_out, "AAA", "DDD", "FFF")) == (
        evaluation("DDD"),
        evaluation("AAA"),
        evaluation("CCC"),
    )


# 寻股


def test_viable_candidates_boundary() -> None:
    """spec「有效候选不足的边界」：负分的不算有效，没有评分的算。"""
    assert _check(scores(SNAPSHOT, HHH=-1)) == (EXPLORATION,)
    assert _check(scores(SNAPSHOT, HHH=None)) == ()


def test_no_candidates() -> None:
    """spec「没有候选」：有效候选 0 只。"""
    assert _check(replace(SNAPSHOT, candidates=())) == (EXPLORATION,)


def test_strong_candidates_boundary() -> None:
    """spec「好候选不足的边界」。"""
    assert _check(scores(SNAPSHOT, DDD=0)) == (EXPLORATION,)
    assert _check(scores(SNAPSHOT, DDD=1)) == ()


def test_most_candidates_expiring_boundary() -> None:
    """spec「多数候选临近过期的边界」：今天之后第 3 个交易日 10-19 的收盘时刻也算。"""
    assert _check(expiries(SNAPSHOT, at_close(10, 19), "AAA", "DDD", "FFF")) == (EXPLORATION,)
    assert _check(expiries(SNAPSHOT, at_close(10, 19), "AAA", "DDD")) == ()
    assert _check(expiries(SNAPSHOT, at_close(10, 20), "AAA", "DDD", "FFF")) == ()


def test_exactly_half_expiring_is_not_most() -> None:
    """spec「刚好一半不算多数」：另有候选 JJJ，一共 6 只。"""
    six = replace(SNAPSHOT, candidates=(*SNAPSHOT.candidates, candidate("JJJ", 0)))

    assert _check(expiries(six, at_close(10, 19), "AAA", "DDD", "FFF")) == ()
    assert _check(expiries(six, at_close(10, 19), "AAA", "DDD", "FFF", "GGG")) == (EXPLORATION,)


@pytest.mark.parametrize(
    ("last", "opened"),
    [(on(10, 9), (EXPLORATION,)), (on(10, 12), ()), (on(10, 15), ())],
    ids=["之后第 3 个交易日是今天", "之后第 3 个交易日是明天", "今天收盘后得出结论"],
)
def test_cooldown_boundary(last: date, opened: tuple[CheckItem, ...]) -> None:
    """spec「冷却期的边界」：上一次寻股的那一天 10-09 之后第 3 个交易日是 10-14。"""
    snapshot = replace(scores(SNAPSHOT, HHH=-1), last_exploration_day=last)

    assert _check(snapshot) == opened


def test_exploration_in_flight() -> None:
    """spec「寻股在排队或运行」：排队的和运行中的在快照里一样，都是在排队或运行的检查项。"""
    assert _check(in_flight(scores(SNAPSHOT, HHH=-1), EXPLORATION)) == ()


def test_exploration_without_a_conclusion_does_not_count() -> None:
    """spec「没有结论的寻股不算」：10-13 收盘开出的寻股因系统故障结束，那一天仍是 10-01。"""
    snapshot = replace(scores(SNAPSHOT, HHH=-1), last_exploration_day=on(10, 1))

    assert _check(snapshot) == (EXPLORATION,)


# 每周复盘：只看输出里有没有每周复盘


def _weekly(today: date, snapshot: CloseSnapshot = SNAPSHOT) -> bool:
    """今天收盘时开不开每周复盘。"""
    return WEEKLY_RETROSPECTIVE in _check(replace(snapshot, today=today))


def test_weekly_after_friday_close() -> None:
    """spec「周五收盘后」。"""
    assert _weekly(on(10, 16))
    assert not _weekly(on(10, 14))


def test_weekly_on_thursday_when_friday_is_a_holiday() -> None:
    """spec「周五休市时是周四」：包括跨年，2026-12-31 和 2027-01-04 在不同的 ISO 年和周。"""
    assert _weekly(on(12, 24))
    assert not _weekly(on(12, 23))
    assert _weekly(on(12, 31))


def test_midweek_holiday() -> None:
    """spec「一周中间休市」：11-26 感恩节休市，11-27 提前收盘。"""
    assert not _weekly(on(11, 25))
    assert _weekly(on(11, 27))


def test_weekly_retrospective_still_queued() -> None:
    """spec「每周复盘在排队」：上周开出的每周复盘还在排队。"""
    assert not _weekly(on(10, 16), in_flight(SNAPSHOT, WEEKLY_RETROSPECTIVE))


# 开的顺序


def test_every_kind_of_item_together() -> None:
    """spec「全部检查项一起开」：快照里股票的先后不影响输出。"""
    snapshot = review_days(replace(SNAPSHOT, today=on(10, 16)), EEE=on(10, 16), BBB=on(10, 16))
    snapshot = deadlines(snapshot, CCC=on(10, 15), BBB=on(10, 16))
    snapshot = new_candidates(scores(snapshot, HHH=-1), "AAA", "DDD")
    shuffled = replace(
        snapshot,
        holdings=tuple(reversed(snapshot.holdings)),
        candidates=tuple(reversed(snapshot.candidates)),
    )
    expected = (
        holding_catalyst_review("CCC"),
        holding_review("EEE"),
        watch_catalyst_review("BBB"),
        evaluation("DDD"),
        evaluation("AAA"),
        EXPLORATION,
        WEEKLY_RETROSPECTIVE,
    )

    assert [s.symbol for s in shuffled.holdings] == ["EEE", "CCC"]
    assert [c.symbol for c in shuffled.candidates] == ["HHH", "GGG", "FFF", "DDD", "AAA"]
    assert _check(snapshot) == expected
    assert _check(shuffled) == expected


# 收盘检查的配置


def test_config_changes_the_check() -> None:
    """spec「改配置改变检查」：今天之后第 8 个交易日是 10-26，第 9 个是 10-27。"""
    cooling = scores(SNAPSHOT, HHH=-1)

    assert _check(
        new_candidates(SNAPSHOT, "AAA", "DDD"), HeartbeatConfig(max_daily_evaluations=1)
    ) == (evaluation("DDD"),)
    assert _check(SNAPSHOT, HeartbeatConfig(min_viable_candidates=6)) == (EXPLORATION,)
    assert _check(SNAPSHOT, HeartbeatConfig(min_strong_candidates=3)) == (EXPLORATION,)
    assert _check(SNAPSHOT, HeartbeatConfig(expiring_sessions=9)) == (EXPLORATION,)
    assert _check(SNAPSHOT, HeartbeatConfig(expiring_sessions=8)) == ()
    cooldown_1 = HeartbeatConfig(exploration_cooldown_sessions=1)
    assert _check(replace(cooling, last_exploration_day=on(10, 13)), cooldown_1) == (EXPLORATION,)
    assert _check(replace(cooling, last_exploration_day=on(10, 14)), cooldown_1) == ()
