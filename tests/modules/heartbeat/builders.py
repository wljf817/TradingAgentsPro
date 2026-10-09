"""定时心跳测试的数据：review-dates spec「检查论点里的日期」和 close-check spec「收盘检查的输入和
输出」里的基准数据。
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from tests.fakes.calendar import sample_nyse_calendar
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.thesis import (
    Catalyst,
    CatalystReference,
    Comparison,
    Condition,
    ConditionKind,
    InvestmentThesis,
    ReferenceKind,
    WatchThesis,
)
from tradingagentspro.modules.heartbeat import (
    CheckItem,
    CloseSnapshot,
    PoolCandidate,
    ThesisDates,
    ThesisUpdate,
)

type Thesis = WatchThesis | InvestmentThesis

NYSE = sample_nyse_calendar()

TODAY = date(2026, 10, 14)  # 周三
LAST_REVIEW_DAY = date(2026, 10, 7)  # 原来的论点的上次复查的那一天
PNL_RATIO = Decimal("0.02")  # 持仓的浮动盈亏比例

# 和 stock-pool spec 的基准关注论点相同
WATCH_THESIS = WatchThesis(
    rationale="新品周期带动收入加速",
    entry_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(50)),
    ),
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    invalidation="毛利率连续两个季度下滑",
    review_day=date(2026, 10, 21),
)
# 和 case-types spec 的基准投资论点相同
INVESTMENT_THESIS = InvestmentThesis(
    rationale="新品周期带动收入加速",
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    target_price=Decimal(60),
    invalidation="毛利率连续两个季度下滑",
    invalidation_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(42)),
    ),
    review_day=date(2026, 10, 21),
)
# 没有写明论点种类的场景，两种论点各跑一遍
BASELINES: tuple[Thesis, ...] = (WATCH_THESIS, INVESTMENT_THESIS)
KINDS = ("关注论点", "投资论点")

# 日程项 E-1（周三）和 E-2（周四）的日期，事件 V-7
E1 = CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="E-1")
E1_DAY = date(2026, 10, 28)
E2 = CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="E-2")
E2_DAY = date(2026, 10, 22)
V7 = CatalystReference(kind=ReferenceKind.EVENT, ref_id="V-7")

NEW_CATALYST = Catalyst(description="Q4 指引上调", deadline=date(2026, 11, 20))


def on(month: int, day: int, year: int = 2026) -> date:
    """日期，默认在 2026 年。"""
    return date(year, month, day)


def review_on(thesis: Thesis, day: date) -> Thesis:
    """论点只把复查日改为 day。"""
    return thesis.model_copy(update={"review_day": day})


def with_catalyst(thesis: Thesis, catalyst: Catalyst) -> Thesis:
    """论点只把催化剂换成 catalyst。"""
    return thesis.model_copy(update={"catalyst": catalyst})


def deadline_on(thesis: Thesis, day: date) -> Thesis:
    """论点只把催化剂截止日期改为 day。"""
    return with_catalyst(thesis, thesis.catalyst.model_copy(update={"deadline": day}))


def referencing(thesis: Thesis, reference: CatalystReference) -> Thesis:
    """论点只把催化剂改为引用 reference，描述和截止日期不变。"""
    return with_catalyst(thesis, thesis.catalyst.model_copy(update={"reference": reference}))


def review_due(thesis: Thesis) -> Thesis:
    """复查日到期的原来论点：复查日改为 2026-10-13。"""
    return review_on(thesis, date(2026, 10, 13))


def catalyst_due(thesis: Thesis) -> Thesis:
    """催化剂到期的原来论点：催化剂截止日期改为 2026-10-13。"""
    return deadline_on(thesis, date(2026, 10, 13))


def new(thesis: Thesis, *, schedule_day: date | None = None) -> ThesisUpdate:
    """新设：没有原来的论点和上次复查的那一天。"""
    return ThesisUpdate(thesis, schedule_day=schedule_day)


def renewal(
    thesis: Thesis,
    previous: Thesis,
    *,
    last_review_day: date = LAST_REVIEW_DAY,
    pnl_ratio: Decimal | None = PNL_RATIO,
    schedule_day: date | None = None,
) -> ThesisUpdate:
    """续期：默认上次复查的那一天是 2026-10-07，浮动盈亏比例 0.02。"""
    return ThesisUpdate(
        thesis,
        previous=previous,
        last_review_day=last_review_day,
        renewal=True,
        pnl_ratio=pnl_ratio,
        schedule_day=schedule_day,
    )


def adjustment(
    thesis: Thesis,
    previous: Thesis,
    *,
    last_review_day: date = LAST_REVIEW_DAY,
    pnl_ratio: Decimal | None = PNL_RATIO,
    schedule_day: date | None = None,
) -> ThesisUpdate:
    """平时调整：默认上次复查的那一天是 2026-10-07，浮动盈亏比例 0.02。"""
    return ThesisUpdate(
        thesis,
        previous=previous,
        last_review_day=last_review_day,
        pnl_ratio=pnl_ratio,
        schedule_day=schedule_day,
    )


# 收盘检查（close-check spec「收盘检查的输入和输出」）：今天也是 2026-10-14（周三）


def at_close(month: int, day: int) -> datetime:
    """2026 年这一天的 20:00Z，2026-11-01 以前纽交所的收盘时刻。"""
    return datetime(2026, month, day, 20, tzinfo=UTC)


EXPIRES_AT = at_close(10, 27)  # 基准候选的过期时间


def _stock(symbol: str) -> ThesisDates:
    """基准的持仓或关注股：复查日 2026-10-21，催化剂截止日期 2026-10-29。"""
    return ThesisDates(symbol, review_day=date(2026, 10, 21), catalyst_deadline=date(2026, 10, 29))


def candidate(symbol: str, score: int | None) -> PoolCandidate:
    """基准过期时间、不是新候选的候选。"""
    return PoolCandidate(symbol, score, EXPIRES_AT, new=False)


# 基准快照：持仓 CCC、EEE，关注 BBB；候选 AAA 评分 1、DDD 评分 2、FFF、GGG、HHH 评分 0；
# 还没有寻股得出过结论；没有在排队或运行的心跳案件
SNAPSHOT = CloseSnapshot(
    today=TODAY,
    holdings=(_stock("CCC"), _stock("EEE")),
    watches=(_stock("BBB"),),
    candidates=(
        candidate("AAA", 1),
        candidate("DDD", 2),
        candidate("FFF", 0),
        candidate("GGG", 0),
        candidate("HHH", 0),
    ),
    last_exploration_day=None,
    in_flight=frozenset(),
)


def review_days(snapshot: CloseSnapshot, **days: date) -> CloseSnapshot:
    """持仓和关注股的复查日改为给出的日期，比如 review_days(SNAPSHOT, CCC=on(10, 14))。"""
    return _change_stocks(snapshot, days, lambda stock, day: replace(stock, review_day=day))


def deadlines(snapshot: CloseSnapshot, **days: date) -> CloseSnapshot:
    """持仓和关注股的催化剂截止日期改为给出的日期。"""
    return _change_stocks(snapshot, days, lambda stock, day: replace(stock, catalyst_deadline=day))


def scores(snapshot: CloseSnapshot, **values: int | None) -> CloseSnapshot:
    """候选的最新评分改为给出的评分，None 是没有评分。"""
    return _change_candidates(snapshot, values, lambda c, score: replace(c, score=score))


def expiries(snapshot: CloseSnapshot, expires_at: datetime, *symbols: str) -> CloseSnapshot:
    """这几只候选的过期时间都改为 expires_at。"""
    return _change_candidates(
        snapshot, dict.fromkeys(symbols, expires_at), lambda c, at: replace(c, expires_at=at)
    )


def new_candidates(snapshot: CloseSnapshot, *symbols: str) -> CloseSnapshot:
    """这几只候选是新候选。"""
    return _change_candidates(
        snapshot, dict.fromkeys(symbols, True), lambda c, new: replace(c, new=new)
    )


def in_flight(snapshot: CloseSnapshot, *items: CheckItem) -> CloseSnapshot:
    """这些检查项开出的案件在排队或运行。"""
    return replace(snapshot, in_flight=snapshot.in_flight | frozenset(items))


def _change_stocks[V](
    snapshot: CloseSnapshot,
    values: Mapping[str, V],
    change: Callable[[ThesisDates, V], ThesisDates],
) -> CloseSnapshot:
    """按股票代码改持仓和关注股；快照里没有的股票报错，免得场景因为写错代码而落空。"""
    assert values.keys() <= {s.symbol for s in (*snapshot.holdings, *snapshot.watches)}

    def apply(stocks: tuple[ThesisDates, ...]) -> tuple[ThesisDates, ...]:
        return tuple(change(s, values[s.symbol]) if s.symbol in values else s for s in stocks)

    return replace(snapshot, holdings=apply(snapshot.holdings), watches=apply(snapshot.watches))


def _change_candidates[V](
    snapshot: CloseSnapshot,
    values: Mapping[str, V],
    change: Callable[[PoolCandidate, V], PoolCandidate],
) -> CloseSnapshot:
    """按股票代码改候选；快照里没有的股票报错。"""
    assert values.keys() <= {c.symbol for c in snapshot.candidates}
    candidates = tuple(
        change(c, values[c.symbol]) if c.symbol in values else c for c in snapshot.candidates
    )
    return replace(snapshot, candidates=candidates)


# 检查项


def holding_review(symbol: str) -> CheckItem:
    return CheckItem(CaseType.HOLDING_REVIEW, symbol)


def holding_catalyst_review(symbol: str) -> CheckItem:
    return CheckItem(CaseType.HOLDING_CATALYST_REVIEW, symbol)


def watch_review(symbol: str) -> CheckItem:
    return CheckItem(CaseType.WATCH_REVIEW, symbol)


def watch_catalyst_review(symbol: str) -> CheckItem:
    return CheckItem(CaseType.WATCH_CATALYST_REVIEW, symbol)


def evaluation(symbol: str) -> CheckItem:
    return CheckItem(CaseType.CANDIDATE_EVALUATION, symbol)


EXPLORATION = CheckItem(CaseType.EXPLORATION)
WEEKLY_RETROSPECTIVE = CheckItem(CaseType.WEEKLY_RETROSPECTIVE)
