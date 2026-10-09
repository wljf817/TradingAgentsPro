"""定时心跳测试的数据：review-dates spec「检查论点里的日期」里的基准数据。"""

from datetime import date
from decimal import Decimal

from tests.fakes.calendar import sample_nyse_calendar
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
from tradingagentspro.modules.heartbeat import ThesisUpdate

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
