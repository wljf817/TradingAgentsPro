"""论点里日期的性质测试（add-review-dates 的 design D9），用纽交所样例日历。

一只接一只的股票：每只从样例日历里随机一个今天，新设一份论点，不通过就在下一步再新设；之后随机一串
步骤：每步把今天往后推 0 到 25 个交易日，随机取续期或平时调整、复查日、催化剂和浮动盈亏比例。只有
通过的论点生效，并按 last_review_day_after 更新上次复查的那一天。今天推过样例日历能覆盖的范围时，
换一只股票从头开始，否则今天停在范围的尽头，后面的步骤再也遇不到到期。每步以后检查 D9 列出的
不变量。参考检查只用 spec 的规则，不读检查的实现。
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from tests.modules.heartbeat.builders import (
    BASELINES,
    INVESTMENT_THESIS,
    NYSE,
    Thesis,
    review_on,
    with_catalyst,
)
from tradingagentspro.contracts.thesis import (
    Catalyst,
    CatalystReference,
    InvestmentThesis,
    ReferenceKind,
)
from tradingagentspro.modules.heartbeat import (
    HeartbeatConfig,
    ThesisUpdate,
    last_review_day_after,
    thesis_dates_rejection,
)

CONFIG = HeartbeatConfig()


def _session_days() -> tuple[date, ...]:
    """样例日历的全部交易日。"""
    days = [NYSE.first_session.day]
    while days[-1] < NYSE.last_session.day:
        days.append(NYSE.next_session(days[-1]).day)
    return tuple(days)


DAYS: Final = _session_days()
# 今天的下标：往前留出 5 个交易日给提前的复查日，往后留出 65 个交易日给最远的截止日期，
# 检查里按交易日推算的日期就都在样例日历之内
FIRST_TODAY: Final = 5
LAST_TODAY: Final = len(DAYS) - 1 - 65

STARTS: Final = st.tuples(st.sampled_from(BASELINES), st.integers(FIRST_TODAY, LAST_TODAY))
# 每步往后推几个交易日：一半的步骤只推 0 到 3 个，同一只股票能走更多步
ADVANCES: Final = st.integers(0, 3) | st.integers(0, 25)
# 复查日和截止日期取今天之后第几个交易日（负数是之前）。复查日多取近的，亏损时续期的复查日才常常
# 落在默认间隔以内；截止日期多取近的，后面的步骤才会遇到到期
REVIEW_OFFSETS: Final = st.integers(-5, 30) | st.integers(1, 6)
DEADLINE_OFFSETS: Final = st.integers(-3, 10) | st.integers(11, 65)
# 日程项在今天之后第几个交易日，截止日期在日程项之后第几个交易日
SCHEDULE_OFFSETS: Final = st.integers(-2, 40)
SCHEDULE_LAGS: Final = st.integers(0, 3)
ONE_IN_FOUR: Final = st.integers(0, 3).map(lambda n: n == 0)  # 四分之一为真
DESCRIPTIONS: Final = ("Q3 财报", "Q4 指引上调", "新品发布")
EVENTS: Final = st.integers(1, 3).map(
    lambda n: CatalystReference(kind=ReferenceKind.EVENT, ref_id=f"V-{n}")
)
# 浮动盈亏比例取亏损幅度的边界附近；None 是查不到，按亏损处理
PNL_RATIOS: Final = st.sampled_from(
    (None, *(Decimal(r) for r in ("0.02", "0.10", "0", "-0.05", "-0.0501", "-0.10")))
)
NEW_CATALYSTS: Final[tuple[str, ...]] = ("换成新的", "引用日程项")
CATALYSTS: Final[tuple[str, ...]] = ("原样保留", "只关联事件", "只改截止日期", *NEW_CATALYSTS)


def _weekend_after(day: date, sunday: bool) -> date:
    """交易日 day 之后的周六或周日。"""
    return day + timedelta(days=5 - day.weekday() + sunday)


class ThesisDatesMachine(RuleBasedStateMachine):
    """一只接一只股票的论点：每只先新设，之后一串续期和平时调整。"""

    def __init__(self) -> None:
        super().__init__()
        self._start((INVESTMENT_THESIS, FIRST_TODAY))

    def _start(self, start: tuple[Thesis, int]) -> None:
        """换一只股票：新设用的基准论点（决定论点种类）和今天在 DAYS 里的下标。"""
        self.baseline, self.index = start
        self.thesis: Thesis | None = None  # 生效的论点；还没有新设通过时为 None
        self.last_review_day: date | None = None
        self.schedules: dict[str, date | None] = {}  # 日程项的日期；None 是调用方查不到

    @initialize(start=STARTS)
    def start(self, start: tuple[Thesis, int]) -> None:
        self._start(start)

    # 随机取值

    def _day(self, data: st.DataObject, offsets: st.SearchStrategy[int], label: str) -> date:
        """今天之后第 offset 个交易日，或者它之后的周末。"""
        day = DAYS[self.index + data.draw(offsets, label=label)]
        if data.draw(ONE_IN_FOUR, label=f"{label}改到周末"):
            return _weekend_after(day, data.draw(st.booleans(), label="周日"))
        return day

    def _schedule(self, data: st.DataObject) -> Catalyst:
        """引用一个新日程项的催化剂：日程项在今天前后，截止日期在日程项之后；查不到日程项时随便取。"""
        ref_id = f"E-{len(self.schedules) + 1}"
        if data.draw(ONE_IN_FOUR, label="查不到日程项"):
            self.schedules[ref_id] = None
            deadline = self._day(data, DEADLINE_OFFSETS, "截止日期")
        else:
            k = self.index + data.draw(SCHEDULE_OFFSETS, label="日程项")
            self.schedules[ref_id] = DAYS[k]
            deadline = DAYS[k + data.draw(SCHEDULE_LAGS, label="日程项之后")]
            if data.draw(ONE_IN_FOUR, label="截止日期改到周末"):
                deadline = _weekend_after(deadline, data.draw(st.booleans(), label="周日"))
        return Catalyst(
            description=data.draw(st.sampled_from(DESCRIPTIONS), label="描述"),
            deadline=deadline,
            reference=CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id=ref_id),
        )

    def _catalyst(self, data: st.DataObject, old: Catalyst | None, today: date) -> Catalyst:
        """新设只取写新的两种；原来的催化剂到期时，四分之三也只取这两种。"""
        choices = CATALYSTS
        if old is None or (old.deadline <= today and not data.draw(ONE_IN_FOUR, label="不换")):
            choices = NEW_CATALYSTS
        choice = data.draw(st.sampled_from(choices), label="催化剂")
        if choice == "引用日程项":
            return self._schedule(data)
        if old is None or choice == "换成新的":
            return Catalyst(
                description=data.draw(st.sampled_from(DESCRIPTIONS), label="描述"),
                deadline=self._day(data, DEADLINE_OFFSETS, "截止日期"),
                reference=data.draw(st.none() | EVENTS, label="引用"),
            )
        if choice == "只关联事件":
            return old.model_copy(update={"reference": data.draw(EVENTS, label="事件")})
        if choice == "只改截止日期":
            deadline = self._day(data, DEADLINE_OFFSETS, "截止日期")
            return old.model_copy(update={"deadline": deadline})
        return old  # 原样保留

    def _update(self, data: st.DataObject, today: date) -> ThesisUpdate:
        """随机一份新写的论点：还没有生效的论点时是新设，否则是续期或平时调整。

        复查日或催化剂截止日期到期时，定时心跳开复查案件，四分之三是续期；都没到期时四分之一。
        """
        previous = self.thesis
        base = self.baseline if previous is None else previous
        renewal = False
        if previous is not None:
            due = previous.review_day <= today or previous.catalyst.deadline <= today
            drawn = data.draw(ONE_IN_FOUR, label="续期")
            renewal = not drawn if due else drawn
        keep_review = previous is not None and data.draw(ONE_IN_FOUR, label="复查日原样保留")
        review_day = base.review_day if keep_review else self._day(data, REVIEW_OFFSETS, "复查日")
        catalyst = self._catalyst(data, None if previous is None else previous.catalyst, today)
        reference = catalyst.reference
        return ThesisUpdate(
            with_catalyst(review_on(base, review_day), catalyst),
            previous=previous,
            last_review_day=self.last_review_day,
            renewal=renewal,
            pnl_ratio=(
                data.draw(PNL_RATIOS, label="浮动盈亏比例")
                if isinstance(base, InvestmentThesis)
                else None
            ),
            schedule_day=(
                self.schedules[reference.ref_id]
                if reference is not None and reference.kind is ReferenceKind.SCHEDULE
                else None
            ),
        )

    # 一步

    @rule(data=st.data())
    def step(self, data: st.DataObject) -> None:
        """今天往后推 0 到 25 个交易日，检查一份新写的论点，通过就生效。"""
        advance = data.draw(ADVANCES, label="往后推")
        if self.index + advance > LAST_TODAY:
            self._start(data.draw(STARTS, label="下一只股票"))
        else:
            self.index += advance
        today = DAYS[self.index]
        update = self._update(data, today)
        rejection = thesis_dates_rejection(update, today, NYSE, CONFIG)
        event(f"情形：{_situation(update)}")
        event(f"拒绝原因：{rejection}" if rejection is not None else "通过")
        if rejection is None:
            _check_passed(update, today)
            self.thesis = update.thesis
            self.last_review_day = last_review_day_after(update, today)

    @invariant()
    def review_day_is_within_the_limit(self) -> None:
        """当前论点的复查日不晚于上次复查那天之后第 20 个交易日（已决定第 1 条）。"""
        if self.thesis is None:
            return
        assert self.last_review_day is not None
        limit = NYSE.add_sessions(self.last_review_day, CONFIG.max_review_sessions).day
        assert self.thesis.review_day <= limit


def _situation(update: ThesisUpdate) -> str:
    if update.previous is None:
        return "新设"
    return "续期" if update.renewal else "平时调整"


def _check_passed(update: ThesisUpdate, today: date) -> None:
    """通过检查的论点满足的性质，按 spec 的规则推出。"""
    thesis, previous = update.thesis, update.previous
    # 改了的复查日（新设和续期的都算）和截止日期都是交易日，都在今天之后
    if previous is None or update.renewal or thesis.review_day != previous.review_day:
        assert thesis.review_day > today
        assert NYSE.is_session(thesis.review_day)
    if previous is None or thesis.catalyst.deadline != previous.catalyst.deadline:
        assert thesis.catalyst.deadline > today
        assert NYSE.is_session(thesis.catalyst.deadline)
    if previous is None:
        return
    in_loss = isinstance(thesis, InvestmentThesis) and (
        update.pnl_ratio is None or update.pnl_ratio < -CONFIG.max_postpone_loss
    )
    if in_loss and update.renewal:
        # 亏损时续期：复查日不晚于今天之后第 5 个交易日（默认间隔）
        event("亏损时续期通过")
        assert thesis.review_day <= NYSE.add_sessions(today, CONFIG.default_review_sessions).day
    elif in_loss:
        # 亏损时平时调整：复查日不晚于原来的
        event("亏损时平时调整通过")
        assert thesis.review_day <= previous.review_day
    old, new = previous.catalyst, thesis.catalyst
    if update.renewal and old.deadline <= today:
        # 原来的催化剂已经到期时续期：截止日期变了，描述或引用也变了
        event("催化剂到期后续期通过")
        assert new.deadline != old.deadline
        assert new.description != old.description or new.reference != old.reference


TestThesisDatesMachine = ThesisDatesMachine.TestCase
