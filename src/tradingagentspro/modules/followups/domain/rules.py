"""跟进审批的规则：审批的检查、触发时间的上限、原因相同、到点和状态变化时作废。

审批按 follow-ups spec「审批的顺序」给出第一项不通过的原因，全部通过时为 None。拒绝是 PM 提议的
正常结果，所以作为返回值，不抛异常，由调用方当作结论校验不通过退回 PM。
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Final

from tradingagentspro.contracts.conclusion import FollowUpReason, FollowUpRequest
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import Catalyst, CatalystReference, Condition, ReferenceKind
from tradingagentspro.modules.followups.config import FollowUpConfig
from tradingagentspro.modules.followups.domain.follow_up import FollowUp
from tradingagentspro.shared.trading_calendar import TradingCalendar


class FollowUpRejection(StrEnum):
    """跟进申请不通过的原因，按 spec「审批的顺序」。"""

    NO_REFERENCE = "no_reference"  # 没有引用具体对象：提示 PM 改为调整复查日
    REFERENCE_MISMATCH = "reference_mismatch"  # 原因和引用的对象不符
    CATALYST_COVERED = "catalyst_covered"  # 催化剂已经覆盖
    TRIGGER_PASSED = "trigger_passed"  # 触发时间已过
    TRIGGER_TOO_FAR = "trigger_too_far"  # 触发时间太远
    STOCK_FULL = "stock_full"  # 待执行的跟进已满
    DAILY_LIMIT = "daily_limit"  # 申请次数已用完


# 引用事件或日程项的两种原因各自要求的引用种类；价格或指标观察引用条件
_REFERENCE_KINDS: Final = {
    FollowUpReason.EVENT_REACTION: ReferenceKind.EVENT,
    FollowUpReason.EXTERNAL_SCHEDULE: ReferenceKind.SCHEDULE,
}

# 状态从高到低；不在股票池也不是持仓（None）最低
_RANKS: Final = {StockStatus.HOLDING: 3, StockStatus.WATCH: 2, StockStatus.CANDIDATE: 1}


def request_rejection(
    request: FollowUpRequest,
    catalyst: Catalyst,
    pending: Sequence[FollowUp],
    approved_today: int,
    now: datetime,
    calendar: TradingCalendar,
    config: FollowUpConfig,
) -> FollowUpRejection | None:
    """审批一个跟进申请，给出第一项不通过的原因，见 spec「审批的顺序」。

    - catalyst：这份结论生效以后这只股票论点里的催化剂
    - pending：这只股票待执行的跟进
    - approved_today：今天已经获批的申请次数
    """
    reference = request.reference
    if reference is None:
        return FollowUpRejection.NO_REFERENCE
    if not _matches(request.reason, reference):
        return FollowUpRejection.REFERENCE_MISMATCH
    if reference == catalyst.reference:
        return FollowUpRejection.CATALYST_COVERED
    if request.trigger_at <= now:
        return FollowUpRejection.TRIGGER_PASSED
    if request.trigger_at >= trigger_limit(now, calendar, config):
        return FollowUpRejection.TRIGGER_TOO_FAR
    if same_reason(request, pending) is None and len(pending) >= config.max_pending_per_stock:
        return FollowUpRejection.STOCK_FULL
    if approved_today >= config.max_daily_requests:
        return FollowUpRejection.DAILY_LIMIT
    return None


def trigger_limit(now: datetime, calendar: TradingCalendar, config: FollowUpConfig) -> datetime:
    """触发时间要早于它：现在归属的交易日之后，第 max_trigger_sessions 个交易日的收盘时刻。

    和收盘时刻比，等价于"触发时间归属的交易日不晚于那一天"，又不用查触发时间本身归属哪天：
    远在日历覆盖范围之外的触发时间只会被拒绝，不会报错。
    """
    today = calendar.session_closing_after(now).day
    return calendar.add_sessions(today, config.max_trigger_sessions).close


def same_reason(request: FollowUpRequest, pending: Iterable[FollowUp]) -> FollowUp | None:
    """原因相同（原因类型相同，且引用同一个对象）的待执行跟进；没有时为 None，最多一个。

    引用用 Pydantic 模型的相等比较：日程项和事件要种类和 ID 都相同，条件要类型、比较和值都相同。
    """
    for follow_up in pending:
        old = follow_up.request
        if old.reason is request.reason and old.reference == request.reference:
            return follow_up
    return None


def due(pending: Iterable[FollowUp], now: datetime) -> list[FollowUp]:
    """触发时间不晚于 now 的待执行跟进，按触发时间、再按获批的先后。"""
    return in_trigger_order(f for f in pending if f.request.trigger_at <= now)


def in_trigger_order(follow_ups: Iterable[FollowUp]) -> list[FollowUp]:
    """按触发时间从早到晚，触发时间相同的按获批的先后。"""
    return sorted(follow_ups, key=lambda f: (f.request.trigger_at, f.requested_at))


def voids_follow_ups(before: StockStatus | None, after: StockStatus | None) -> bool:
    """状态变到更低时作废这只股票待执行的跟进：清仓、关注降为候选、移出股票池。

    before 和 after 是变化之前和之后的状态，不在股票池也不是持仓时为 None。
    """
    return _rank(after) < _rank(before)


def _matches(reason: FollowUpReason, reference: CatalystReference | Condition) -> bool:
    """事件后反应引用事件，外部日程引用日程项，价格或指标观察引用代码能检测的条件。"""
    if isinstance(reference, Condition):
        return reason is FollowUpReason.OBSERVATION
    return _REFERENCE_KINDS.get(reason) is reference.kind


def _rank(status: StockStatus | None) -> int:
    return 0 if status is None else _RANKS[status]
