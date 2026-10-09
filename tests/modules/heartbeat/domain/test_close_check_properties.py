"""收盘检查的性质测试（add-close-check 的 design D11），用纽交所样例日历。

随机一份快照和配置：今天从样例日历里随机；0 到 5 只持仓和关注、0 到 8 只候选，股票代码互不相同；
复查日和催化剂截止日期在今天前后 10 个交易日以内，评分为空或 -2 到 2，过期时间是今天之前 2 个到之后
12 个交易日的收盘时刻；在排队或运行的检查项只用这些股票，每只持仓和关注最多一个复查类。参考检查只用
spec 的规则，不读收盘检查的实现。
"""

from dataclasses import replace
from typing import Final

from hypothesis import event, given
from hypothesis import strategies as st

from tests.modules.heartbeat.builders import NYSE
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.modules.heartbeat import (
    CheckItem,
    CloseSnapshot,
    HeartbeatConfig,
    PoolCandidate,
    ThesisDates,
    close_check,
)
from tradingagentspro.modules.scheduler import HeartbeatTrigger
from tradingagentspro.shared.trading_calendar import TradingSession


def _sessions() -> tuple[TradingSession, ...]:
    """样例日历的全部交易时段。"""
    sessions = [NYSE.first_session]
    while sessions[-1] != NYSE.last_session:
        sessions.append(NYSE.next_session(sessions[-1].day))
    return tuple(sessions)


SESSIONS: Final = _sessions()
# 今天的下标：往前留出 10 个交易日给到期的日期和上一次寻股，往后留出 12 个交易日给过期时间，
# 按交易日推算的日期就都在样例日历之内
FIRST_TODAY: Final = 10
LAST_TODAY: Final = len(SESSIONS) - 1 - 12
SYMBOLS: Final = tuple(letter * 3 for letter in "ABCDEFGHIJKLMNOPQR")  # 最多 5 + 5 + 8 只

EVALUATION: Final = CaseType.CANDIDATE_EVALUATION
EXPLORATION: Final = CheckItem(CaseType.EXPLORATION)
WEEKLY_RETROSPECTIVE: Final = CheckItem(CaseType.WEEKLY_RETROSPECTIVE)
# 复查日到期开的、催化剂截止日期到期开的
HOLDING_TYPES: Final = (CaseType.HOLDING_REVIEW, CaseType.HOLDING_CATALYST_REVIEW)
WATCH_TYPES: Final = (CaseType.WATCH_REVIEW, CaseType.WATCH_CATALYST_REVIEW)
REVIEW_TYPES: Final = frozenset((*HOLDING_TYPES, *WATCH_TYPES))
# spec「开的顺序」的表：检查项属于第几组
GROUPS: Final = {
    CaseType.HOLDING_REVIEW: 1,
    CaseType.HOLDING_CATALYST_REVIEW: 1,
    CaseType.WATCH_REVIEW: 2,
    CaseType.WATCH_CATALYST_REVIEW: 2,
    CaseType.CANDIDATE_EVALUATION: 3,
    CaseType.EXPLORATION: 4,
    CaseType.WEEKLY_RETROSPECTIVE: 5,
}

ONE_IN_FOUR: Final = st.integers(0, 3).map(lambda n: n == 0)  # 四分之一为真
SCORES: Final = st.none() | st.integers(-2, 2)
CONFIGS: Final = st.builds(
    HeartbeatConfig,
    max_daily_evaluations=st.integers(1, 4),
    min_viable_candidates=st.integers(1, 7),
    min_strong_candidates=st.integers(1, 4),
    expiring_sessions=st.integers(1, 5),
    exploration_cooldown_sessions=st.integers(1, 5),
)


@st.composite
def _snapshots(draw: st.DrawFn) -> CloseSnapshot:
    index = draw(st.integers(FIRST_TODAY, LAST_TODAY), label="今天")
    symbols = iter(draw(st.permutations(SYMBOLS), label="股票代码"))
    in_flight: set[CheckItem] = set()

    def stocks(count: int, types: tuple[CaseType, CaseType]) -> tuple[ThesisDates, ...]:
        """count 只持仓或关注；五分之二有一个复查类在排队或运行。"""
        result = []
        for _ in range(count):
            symbol = next(symbols)
            review_day, deadline = draw(st.tuples(st.integers(-10, 10), st.integers(-10, 10)))
            result.append(
                ThesisDates(
                    symbol, SESSIONS[index + review_day].day, SESSIONS[index + deadline].day
                )
            )
            busy = draw(st.sampled_from((None, None, None, *types)), label="在排队或运行")
            if busy is not None:
                in_flight.add(CheckItem(busy, symbol))
        return tuple(result)

    holdings = stocks(draw(st.integers(0, 5), label="持仓"), HOLDING_TYPES)
    watches = stocks(draw(st.integers(0, 5), label="关注"), WATCH_TYPES)
    candidates = []
    for _ in range(draw(st.integers(0, 8), label="候选")):
        symbol = next(symbols)
        expires = SESSIONS[index + draw(st.integers(-2, 12), label="过期")].close
        candidates.append(PoolCandidate(symbol, draw(SCORES), expires, draw(st.booleans())))
        if draw(ONE_IN_FOUR, label="候选评估在排队或运行"):
            in_flight.add(CheckItem(EVALUATION, symbol))
    for item in (EXPLORATION, WEEKLY_RETROSPECTIVE):
        if draw(ONE_IN_FOUR, label=f"{item.case_type}在排队或运行"):
            in_flight.add(item)
    last = draw(st.none() | st.integers(-10, 1), label="上一次寻股")
    return CloseSnapshot(
        today=SESSIONS[index].day,
        holdings=holdings,
        watches=watches,
        candidates=tuple(candidates),
        last_exploration_day=None if last is None else SESSIONS[index + last].day,
        in_flight=frozenset(in_flight),
    )


@given(snapshot=_snapshots(), config=CONFIGS)
def test_items_follow_the_spec(snapshot: CloseSnapshot, config: HeartbeatConfig) -> None:
    """D11 的不变量：每只股票最多一个、不重复开、到期就开、候选评估的个数和顺序、输出的分组。"""
    items = close_check(snapshot, NYSE, config)
    _record_events(snapshot, config, items)

    # 每只股票最多一个检查项，寻股和每周复盘各最多一个
    assert len(set(items)) == len(items)
    symbols = [item.symbol for item in items if item.symbol is not None]
    assert len(set(symbols)) == len(symbols)
    # 没有检查项在排队或运行；复查类按 spec 的表，被挡的股票没有
    assert not set(items) & snapshot.in_flight
    assert {item for item in items if item.case_type in REVIEW_TYPES} == _due_reviews(snapshot)
    # 候选评估：上限减在排队或运行的个数和等着的新候选个数中小的那个，按 spec 的顺序排在最前
    evaluated = [item.symbol for item in items if item.case_type is EVALUATION]
    assert evaluated == _first_evaluations(snapshot, config)
    # 按持仓、关注、候选评估、寻股、每周复盘分组，持仓和关注里按股票代码
    groups = [GROUPS[item.case_type] for item in items]
    assert groups == sorted(groups)
    for group in (1, 2):
        in_group = [item.symbol or "" for item in items if GROUPS[item.case_type] == group]
        assert in_group == sorted(in_group)
    # 每个检查项都能构造成调度器的定时心跳触发
    for item in items:
        HeartbeatTrigger(item.case_type, item.symbol)


@given(snapshot=_snapshots(), config=CONFIGS, data=st.data())
def test_snapshot_order_does_not_matter(
    snapshot: CloseSnapshot, config: HeartbeatConfig, data: st.DataObject
) -> None:
    """打乱快照里各元组的顺序，输出不变。"""
    shuffled = replace(
        snapshot,
        holdings=tuple(data.draw(st.permutations(snapshot.holdings), label="持仓的顺序")),
        watches=tuple(data.draw(st.permutations(snapshot.watches), label="关注的顺序")),
        candidates=tuple(data.draw(st.permutations(snapshot.candidates), label="候选的顺序")),
    )

    assert close_check(shuffled, NYSE, config) == close_check(snapshot, NYSE, config)


def _due_reviews(snapshot: CloseSnapshot) -> set[CheckItem]:
    """spec「复查日和催化剂截止日期到期」的表，去掉有复查类在排队或运行的股票。"""
    busy = {item.symbol for item in snapshot.in_flight if item.case_type in REVIEW_TYPES}
    due: set[CheckItem] = set()
    for stocks, (review, catalyst_review) in (
        (snapshot.holdings, HOLDING_TYPES),
        (snapshot.watches, WATCH_TYPES),
    ):
        for stock in stocks:
            if (
                stock.symbol in busy
                or min(stock.review_day, stock.catalyst_deadline) > snapshot.today
            ):
                continue
            catalyst_due = stock.catalyst_deadline <= snapshot.today
            due.add(CheckItem(catalyst_review if catalyst_due else review, stock.symbol))
    return due


def _first_evaluations(snapshot: CloseSnapshot, config: HeartbeatConfig) -> list[str]:
    """spec「候选评估」：等着的新候选按顺序表排好，取空出的名额那么多只。

    顺序表用稳定排序从最后一项排起：股票代码、过期时间、评分从高到低（没有评分的当作低于 -2）。
    """
    queued = {item.symbol for item in snapshot.in_flight if item.case_type is EVALUATION}
    ranked = sorted(
        (c for c in snapshot.candidates if c.new and c.symbol not in queued),
        key=lambda c: c.symbol,
    )
    ranked.sort(key=lambda c: c.expires_at)
    ranked.sort(key=lambda c: -3 if c.score is None else c.score, reverse=True)
    slots = max(config.max_daily_evaluations - len(queued), 0)
    return [c.symbol for c in ranked[: min(slots, len(ranked))]]


def _record_events(
    snapshot: CloseSnapshot, config: HeartbeatConfig, items: tuple[CheckItem, ...]
) -> None:
    """统计样例：开出过哪些检查项、在排队或运行挡住开的情况、候选评估被上限截断的情况。"""
    for item in items:
        event(f"开出：{item.case_type}")
    busy = {item.symbol for item in snapshot.in_flight if item.case_type in REVIEW_TYPES}
    if any(
        stock.symbol in busy and min(stock.review_day, stock.catalyst_deadline) <= snapshot.today
        for stock in (*snapshot.holdings, *snapshot.watches)
    ):
        event("挡住：到期的复查类")
    queued = {item.symbol for item in snapshot.in_flight if item.case_type is EVALUATION}
    if any(c.new and c.symbol in queued for c in snapshot.candidates):
        event("挡住：新候选的候选评估")
    waiting = sum(1 for c in snapshot.candidates if c.new and c.symbol not in queued)
    if waiting > max(config.max_daily_evaluations - len(queued), 0):
        event("候选评估被上限截断")
    # 去掉在排队或运行的这一项以后会开，才算挡住
    for item in (EXPLORATION, WEEKLY_RETROSPECTIVE):
        if item in snapshot.in_flight:
            unblocked = replace(snapshot, in_flight=snapshot.in_flight - {item})
            if item in close_check(unblocked, NYSE, config):
                event(f"挡住：{item.case_type}")
