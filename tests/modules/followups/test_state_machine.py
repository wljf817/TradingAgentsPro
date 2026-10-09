"""跟进审批的状态机性质测试（add-follow-ups 的 design D13）。

随机交错申请、拨动时间（跨收盘、跨周末）、到点触发和股票状态变化，每一步之后检查：每只股票待执行
的跟进不超过上限、原因相同的最多一个；每个交易日获批的申请不超过上限；被拒绝的申请不改变记录；
获批的跟进通过审批的各项检查；到点触发以后没有到点的待执行跟进，每个跟进最多给出一次；不再待执行
的跟进不再变化；状态往下走以后这只股票没有待执行的跟进，候选和不在股票池的股票没有待执行的跟进。
配置用较小的上限，让上限经常被碰到。
"""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from tests.fakes.clock import FakeClock
from tests.fakes.follow_ups import InMemoryFollowUpStore
from tests.fakes.ids import FakeIds
from tests.modules.followups.builders import BASELINE_NOW, NYSE
from tradingagentspro.contracts.conclusion import FollowUpReason, FollowUpRequest
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import (
    Catalyst,
    CatalystReference,
    Comparison,
    Condition,
    ConditionKind,
    ReferenceKind,
)
from tradingagentspro.modules.followups import (
    FollowUp,
    FollowUpConfig,
    FollowUpRejection,
    FollowUpService,
    FollowUpStatus,
)

CANDIDATE, WATCH, HOLDING = StockStatus.CANDIDATE, StockStatus.WATCH, StockStatus.HOLDING
PENDING, TRIGGERED = FollowUpStatus.PENDING, FollowUpStatus.TRIGGERED
EVENT_REACTION, EXTERNAL_SCHEDULE, OBSERVATION = (
    FollowUpReason.EVENT_REACTION,
    FollowUpReason.EXTERNAL_SCHEDULE,
    FollowUpReason.OBSERVATION,
)
CONFIG = FollowUpConfig(max_pending_per_stock=2, max_trigger_sessions=5, max_daily_requests=3)
# 现在不晚于它：之后第 max_trigger_sessions 个交易日还在样例日历之内
LATEST = datetime(2027, 1, 4, 15, tzinfo=UTC)
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

type Reference = CatalystReference | Condition


def _schedule(ref_id: str) -> CatalystReference:
    return CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id=ref_id)


def _event(ref_id: str) -> CatalystReference:
    return CatalystReference(kind=ReferenceKind.EVENT, ref_id=ref_id)


def _price(value: int) -> Condition:
    return Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_LEAST, value=Decimal(value))


V1, V2, S1, S2 = _event("V-1"), _event("V-2"), _schedule("S-1"), _schedule("S-2")
P55, P56 = _price(55), _price(56)
# 每种原因对应的引用；S-1、S-2、V-1 也是某只股票的催化剂引用的对象
MATCHING: dict[FollowUpReason, tuple[Reference, ...]] = {
    EVENT_REACTION: (V1, V2),
    EXTERNAL_SCHEDULE: (S1, S2),
    OBSERVATION: (P55, P56),
}
REFERENCES: tuple[Reference | None, ...] = (None, V1, V2, S1, S2, P55, P56)

# 股票一开始的状态和固定的催化剂：AAA 是候选，BBB 是关注，CCC 是持仓，DDD 不在股票池
SYMBOLS = ("AAA", "BBB", "CCC", "DDD")
INITIAL: dict[str, StockStatus | None] = {
    "AAA": CANDIDATE,
    "BBB": WATCH,
    "CCC": HOLDING,
    "DDD": None,
}
CATALYSTS = {
    symbol: Catalyst(description="催化剂", deadline=date(2027, 1, 29), reference=reference)
    for symbol, reference in zip(SYMBOLS, (V1, None, S1, S2), strict=True)
}
CASES = tuple(UUID(int=0xC000 + n) for n in range(1, 4))

# stock-pool spec「状态变化」表：每种状态能变成的状态（候选保持不改变状态，不列）
TRANSITIONS: dict[StockStatus | None, tuple[StockStatus | None, ...]] = {
    None: (CANDIDATE,),
    CANDIDATE: (WATCH, None, HOLDING),
    WATCH: (CANDIDATE, None, HOLDING),
    HOLDING: (HOLDING, CANDIDATE, WATCH, None),
}
# follow-ups spec「股票状态往下走时作废」表里作废的变化（候选变为不在股票池时没有待执行的跟进）
DOWNWARD = {
    (HOLDING, CANDIDATE),
    (HOLDING, WATCH),
    (HOLDING, None),
    (WATCH, CANDIDATE),
    (WATCH, None),
    (CANDIDATE, None),
}

# 触发时间：现在之前或正好现在、近处（几天以内），或者上限前后几秒
# 近处的最多，让获批的申请多到常常碰上每只股票和每天的上限
TRIGGERS = st.one_of(
    st.tuples(st.just("now"), st.integers(-3 * 24 * 60, 0)),
    st.tuples(st.just("now"), st.integers(1, 5 * 24 * 60)),
    st.tuples(st.just("now"), st.integers(1, 5 * 24 * 60)),
    st.tuples(st.just("now"), st.integers(1, 5 * 24 * 60)),
    st.tuples(st.just("limit"), st.sampled_from([-60, -1, 0, 1])),
)

_REJECTION_EVENTS = {
    FollowUpRejection.NO_REFERENCE: "拒绝：没有引用具体对象",
    FollowUpRejection.REFERENCE_MISMATCH: "拒绝：原因和引用的对象不符",
    FollowUpRejection.CATALYST_COVERED: "拒绝：催化剂已经覆盖",
    FollowUpRejection.TRIGGER_PASSED: "拒绝：触发时间已过",
    FollowUpRejection.TRIGGER_TOO_FAR: "拒绝：触发时间太远",
    FollowUpRejection.STOCK_FULL: "拒绝：待执行的跟进已满",
    FollowUpRejection.DAILY_LIMIT: "拒绝：申请次数已用完",
}


def _day(moment: datetime) -> date:
    """moment 归属的交易日。"""
    return NYSE.session_closing_after(moment).day


def _limit(moment: datetime) -> datetime:
    """在 moment 审批时触发时间的上限：归属的交易日之后第 max_trigger_sessions 个交易日收盘。"""
    return NYSE.add_sessions(_day(moment), CONFIG.max_trigger_sessions).close


def _matches(reason: FollowUpReason, reference: Reference) -> bool:
    """spec「引用的对象」表：事件后反应引用事件，外部日程引用日程项，价格或指标观察引用条件。
    引用都取自 REFERENCES，所以按 MATCHING 查。
    """
    return any(reference == r for r in MATCHING[reason])


def _same_reason(a: FollowUpRequest, b: FollowUpRequest) -> bool:
    return a.reason is b.reason and a.reference == b.reference


class FollowUpMachine(RuleBasedStateMachine):
    """参考模型自己记下每只股票的状态、每个交易日获批的次数、给出过的跟进和不再待执行的跟进，
    检查时只读存储里的记录，不读审批的实现。
    """

    def __init__(self) -> None:
        super().__init__()
        self.clock = FakeClock(BASELINE_NOW)
        self.store = InMemoryFollowUpStore()
        self.service = FollowUpService(self.store, self.clock, NYSE, FakeIds(), CONFIG)
        self.statuses = dict(INITIAL)
        self.approved: Counter[date] = Counter()  # 每个交易日获批的申请
        self.given: set[UUID] = set()  # 到点触发给出过的跟进
        self.settled: dict[UUID, FollowUp] = {}  # 第一次看到不再待执行时的记录

    @property
    def now(self) -> datetime:
        return self.clock.now()

    def _records(self) -> dict[UUID, FollowUp]:
        return {f.follow_up_id: f for f in self.store.requested_since(EPOCH)}

    def _pending(self, symbol: str) -> list[FollowUp]:
        return [f for f in self.store.pending() if f.symbol == symbol]

    # 申请

    @rule(
        data=st.data(),
        pick=st.integers(0, 9),
        reason=st.sampled_from(FollowUpReason),
        case_id=st.sampled_from(CASES),
    )
    def request(
        self, data: st.DataObject, pick: int, reason: FollowUpReason, case_id: UUID
    ) -> None:
        """只有持仓和关注的结论能附跟进申请，没有持仓和关注时什么也不做。引用大多取和原因对应的，
        有时任取（包括不写）。
        """
        eligible = [s for s in SYMBOLS if self.statuses[s] in (HOLDING, WATCH)]
        if not eligible:
            return
        symbol = eligible[pick % len(eligible)]
        matching = data.draw(st.integers(0, 3)) > 0
        reference = data.draw(st.sampled_from(MATCHING[reason] if matching else REFERENCES))
        base, amount = data.draw(TRIGGERS)
        if base == "now":
            trigger_at = self.now + timedelta(minutes=amount)
        else:
            trigger_at = _limit(self.now) + timedelta(seconds=amount)
        request = FollowUpRequest(
            reason=reason,
            reference=reference,
            question="之后反应如何？",
            possible_actions="加仓或减仓",
            trigger_at=trigger_at,
        )
        catalyst = CATALYSTS[symbol]
        before = self._records()
        pending = self._pending(symbol)
        same = [f for f in pending if _same_reason(f.request, request)]
        today = _day(self.now)

        expected = self._expected(request, catalyst, pending, same, today)

        result = self.service.request(symbol, request, case_id, catalyst)

        after = self._records()
        if isinstance(result, FollowUpRejection):
            assert result is expected
            assert after == before, "被拒绝的申请不能改变记录"
            event(_REJECTION_EVENTS[result])
            return
        assert expected is None, f"应该被拒绝：{expected}"
        self.approved[today] += 1
        assert result.follow_up_id not in before
        assert result == FollowUp(result.follow_up_id, symbol, request, case_id, self.now)
        # 只新增这一条，原因相同的旧跟进被替换
        changed = {i for i, f in after.items() if before.get(i) != f}
        assert changed == {result.follow_up_id} | {f.follow_up_id for f in same}
        for old in same:
            assert after[old.follow_up_id] == old.replaced()
            event("替换")

    def _expected(
        self,
        request: FollowUpRequest,
        catalyst: Catalyst,
        pending: list[FollowUp],
        same: list[FollowUp],
        today: date,
    ) -> FollowUpRejection | None:
        """按 spec「审批的顺序」从参考模型推出的结果：第一项不通过的原因，全部通过时为 None。"""
        reference = request.reference
        checks = [
            (FollowUpRejection.NO_REFERENCE, reference is None),
            (
                FollowUpRejection.REFERENCE_MISMATCH,
                reference is not None and not _matches(request.reason, reference),
            ),
            (FollowUpRejection.CATALYST_COVERED, reference == catalyst.reference),
            (FollowUpRejection.TRIGGER_PASSED, request.trigger_at <= self.now),
            (FollowUpRejection.TRIGGER_TOO_FAR, request.trigger_at >= _limit(self.now)),
            (
                FollowUpRejection.STOCK_FULL,
                not same and len(pending) >= CONFIG.max_pending_per_stock,
            ),
            (FollowUpRejection.DAILY_LIMIT, self.approved[today] >= CONFIG.max_daily_requests),
        ]
        return next((rejection for rejection, fails in checks if fails), None)

    # 到点触发

    @rule()
    def trigger_due(self) -> None:
        before = self._records()
        expected = {
            f.follow_up_id for f in self.store.pending() if f.request.trigger_at <= self.now
        }

        given = self.service.trigger_due()

        ids = [f.follow_up_id for f in given]
        assert set(ids) == expected and len(ids) == len(expected)
        assert not self.given & expected, "每个跟进最多被给出一次"
        self.given |= expected
        order = [(f.request.trigger_at, f.requested_at) for f in given]
        assert order == sorted(order)
        assert all(f == before[f.follow_up_id].triggered() for f in given)
        assert all(f.request.trigger_at > self.now for f in self.store.pending())
        if given:
            event("到点触发")

    # 股票状态变化

    @rule(symbol=st.sampled_from(SYMBOLS), pick=st.integers(0, 9))
    def change_status(self, symbol: str, pick: int) -> None:
        """按 stock-pool spec「状态变化」表从这只股票现在的状态随机选一个合法的变化。"""
        before_status = self.statuses[symbol]
        choices = TRANSITIONS[before_status]
        after_status = choices[pick % len(choices)]
        before = self._records()
        pending = {f.follow_up_id for f in self._pending(symbol)}

        voided = self.service.status_changed(symbol, before_status, after_status)

        self.statuses[symbol] = after_status
        after = self._records()
        changed = {i for i, f in after.items() if before[i] != f}
        if (before_status, after_status) in DOWNWARD:
            assert {f.follow_up_id for f in voided} == pending == changed
            assert all(f == before[f.follow_up_id].voided() for f in voided)
            assert not self._pending(symbol), "状态往下走以后不能有待执行的跟进"
            if voided:
                event("状态往下走时作废")
        else:
            assert voided == ()
            assert changed == set()

    # 拨动时间

    @rule(minutes=st.integers(1, 24 * 60))
    def advance(self, minutes: int) -> None:
        """往后拨最多一天，常常跨过收盘，几步之后跨过周末。"""
        target = min(self.now + timedelta(minutes=minutes), LATEST)
        if _day(target).weekday() < _day(self.now).weekday():
            event("跨过周末")
        self.clock.set(target)

    @rule(seconds=st.sampled_from([-1, 0, 1]))
    def to_the_close(self, seconds: int) -> None:
        """拨到当前交易日收盘的前后一秒或收盘时刻，碰归属的交易日的边界。"""
        close = NYSE.session_closing_after(self.now).close
        target = close + timedelta(seconds=seconds)
        self.clock.set(min(target if target > self.now else close, LATEST))

    @rule(seconds=st.sampled_from([-1, 0]))
    def to_a_trigger(self, seconds: int) -> None:
        """拨到最早的待执行跟进的触发时间或它的前一秒，碰到点的边界；没有待执行的跟进时不拨。"""
        pending = self.store.pending()
        if not pending:
            return
        earliest = min(f.request.trigger_at for f in pending)
        target = earliest + timedelta(seconds=seconds)
        if target > self.now:
            self.clock.set(min(target, LATEST))

    # 不变量

    @invariant()
    def pending_within_the_stock_limit(self) -> None:
        """每只股票待执行的跟进不超过上限，原因相同的最多一个。"""
        for symbol in SYMBOLS:
            pending = self._pending(symbol)
            assert len(pending) <= CONFIG.max_pending_per_stock
            for i, a in enumerate(pending):
                assert not any(_same_reason(a.request, b.request) for b in pending[i + 1 :])

    @invariant()
    def approved_within_the_daily_limit(self) -> None:
        """每个交易日获批的申请不超过上限；记录的获批时刻数出来的次数和参考模型一致。"""
        counted = Counter(_day(f.requested_at) for f in self._records().values())
        assert counted == self.approved
        assert all(n <= CONFIG.max_daily_requests for n in counted.values())

    @invariant()
    def records_pass_the_checks(self) -> None:
        """每个获批的跟进：引用和原因对应、不等于催化剂的引用、触发时间在获批时刻之后并且早于
        那时的上限。
        """
        for f in self._records().values():
            reference = f.request.reference
            assert reference is not None and _matches(f.request.reason, reference)
            assert reference != CATALYSTS[f.symbol].reference
            assert f.requested_at < f.request.trigger_at < _limit(f.requested_at)

    @invariant()
    def settled_do_not_change(self) -> None:
        """已触发、被替换、已作废的跟进不再变化；已触发的都是到点触发给出过的。"""
        for f in self._records().values():
            if f.follow_up_id in self.settled:
                assert f == self.settled[f.follow_up_id]
            elif f.status is not PENDING:
                self.settled[f.follow_up_id] = f
            assert (f.status is TRIGGERED) == (f.follow_up_id in self.given)

    @invariant()
    def only_watch_and_holding_have_pending(self) -> None:
        for f in self.store.pending():
            assert self.statuses[f.symbol] in (HOLDING, WATCH)

    @invariant()
    def pending_by_symbol_agrees(self) -> None:
        """按股票给出的待执行跟进就是存储里这只股票待执行的，按触发时间、再按获批的先后。"""
        for symbol in SYMBOLS:
            pending = self.service.pending(symbol)
            assert set(pending) == set(self._pending(symbol))
            order = [(f.request.trigger_at, f.requested_at) for f in pending]
            assert order == sorted(order)


TestFollowUpMachine = FollowUpMachine.TestCase
