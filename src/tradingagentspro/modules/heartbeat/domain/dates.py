"""论点里的日期：检查 PM 新写的论点里的复查日和催化剂截止日期，见 review-dates spec。

情形由输入推出：没有原来的论点是新设（买入、升级、清仓转为关注）；有原来的论点时，复查类案件里
的续期和其他案件里的平时调整各有规则。论点更新是整份新论点，改了哪一项由和原来的论点比较得出。
拒绝是 PM 提交的正常结果，所以作为返回值，不抛异常，由调用方当作结论校验不通过退回 PM。
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from tradingagentspro.contracts.thesis import (
    Catalyst,
    InvestmentThesis,
    ReferenceKind,
    WatchThesis,
)
from tradingagentspro.modules.heartbeat.config import HeartbeatConfig
from tradingagentspro.shared.trading_calendar import TradingCalendar

type Thesis = WatchThesis | InvestmentThesis


@dataclass(frozen=True, slots=True)
class ThesisUpdate:
    """PM 新写的一份论点，以及检查它的日期要用到的事实。"""

    thesis: Thesis
    # 原来的论点和上次复查的那一天；新设（买入、升级、清仓转为关注）时都是 None
    previous: Thesis | None = None
    last_review_day: date | None = None
    renewal: bool = False  # 复查类案件里的续期
    # 持仓的浮动盈亏比例 = (最新价 - 均价) / 均价；只有投资论点的续期和平时调整用到，None 是查不到
    pnl_ratio: Decimal | None = None
    # 新论点的催化剂引用日程项时，这个日程项的日期；None 是查不到
    schedule_day: date | None = None

    def __post_init__(self) -> None:
        if (self.previous is None) != (self.last_review_day is None):
            raise ValueError("原来的论点和上次复查的那一天必须同时传入")
        if self.previous is None:
            if self.renewal:
                raise ValueError("续期一定有原来的论点")
        elif type(self.previous) is not type(self.thesis):
            raise ValueError("原来的论点和新论点不是同一种论点")


class ThesisDatesRejection(StrEnum):
    """论点里的日期不通过的原因，顺序见 spec「检查的顺序」。"""

    REVIEW_TOO_SOON = "review_too_soon"  # 复查日太近
    POSTPONED_IN_LOSS = "postponed_in_loss"  # 亏损时不能推后复查日
    REVIEW_TOO_LATE = "review_too_late"  # 复查日太远
    REVIEW_NOT_SESSION = "review_not_session"  # 复查日不是交易日
    STALE_CATALYST = "stale_catalyst"  # 没有写新的催化剂
    DEADLINE_TOO_SOON = "deadline_too_soon"  # 截止日期太近
    UNKNOWN_SCHEDULE = "unknown_schedule"  # 引用的日程项不存在
    OFF_SCHEDULE = "off_schedule"  # 截止日期和日程项不符
    DEADLINE_TOO_LATE = "deadline_too_late"  # 截止日期太远
    DEADLINE_NOT_SESSION = "deadline_not_session"  # 截止日期不是交易日


def thesis_dates_rejection(
    update: ThesisUpdate, today: date, calendar: TradingCalendar, config: HeartbeatConfig
) -> ThesisDatesRejection | None:
    """按 spec「检查的顺序」给出第一项不通过的原因，全部通过时为 None。

    today 是现在归属的交易日，不是交易日时抛 ValueError。
    """
    if not calendar.is_session(today):
        raise ValueError(f"{today} 不是交易日")
    rejection = _review_day_rejection(update, today, calendar, config)
    if rejection is not None:
        return rejection
    return _catalyst_rejection(update, today, calendar, config)


def last_review_day_after(update: ThesisUpdate, today: date) -> date:
    """论点通过检查、生效以后的上次复查的那一天：新设和续期是今天，平时调整不变。

    只在论点通过检查以后调用；不通过时论点不生效，存着的上次复查的那一天也不变。买入那一天是
    给出开仓方案结论的那一天，不等成交；加仓不是新设。
    """
    if update.last_review_day is None or update.renewal:
        return today
    return update.last_review_day


def _review_day_rejection(
    update: ThesisUpdate, today: date, calendar: TradingCalendar, config: HeartbeatConfig
) -> ThesisDatesRejection | None:
    """复查日的检查，spec「检查的顺序」第 1 到 4 项。

    先比较范围、再查交易日：PM 写的日期可能超出日历的覆盖范围，比较范围只用到从今天和上次复查
    那天推出的日期；查交易日时日期已经在范围内。
    """
    day = update.thesis.review_day
    previous = update.previous
    if previous is None or update.renewal:
        # 新设和续期：今天之后第 min 到第 max 个交易日；续期不要求和原来的不同
        if day < calendar.add_sessions(today, config.min_review_sessions).day:
            return ThesisDatesRejection.REVIEW_TOO_SOON
        # 亏损的持仓续期：不晚于今天之后第 default 个交易日。新设还没有持仓
        if (
            previous is not None
            and _in_loss(update, config)
            and day > calendar.add_sessions(today, config.default_review_sessions).day
        ):
            return ThesisDatesRejection.POSTPONED_IN_LOSS
        if day > calendar.add_sessions(today, config.max_review_sessions).day:
            return ThesisDatesRejection.REVIEW_TOO_LATE
    elif day == previous.review_day:
        return None  # 平时调整里原样保留，不检查，包括已经到期的
    else:
        # 平时调整：最早调到下一个交易日。只有推后才查亏损和上限：提前的日期早于原来的，
        # 原来的已经在上限以内
        if day <= today:
            return ThesisDatesRejection.REVIEW_TOO_SOON
        if day > previous.review_day:
            if _in_loss(update, config):
                return ThesisDatesRejection.POSTPONED_IN_LOSS
            # 上限从上次复查那天算：否则每个事件案件都能再往后推，就一直不复查了
            assert update.last_review_day is not None  # 和 previous 同时传入
            limit = calendar.add_sessions(update.last_review_day, config.max_review_sessions)
            if day > limit.day:
                return ThesisDatesRejection.REVIEW_TOO_LATE
    if not calendar.is_session(day):
        return ThesisDatesRejection.REVIEW_NOT_SESSION
    return None


def _in_loss(update: ThesisUpdate, config: HeartbeatConfig) -> bool:
    """持仓亏损超过阈值，等于阈值不算；查不到浮动盈亏时按亏损处理。关注论点没有持仓。"""
    if isinstance(update.thesis, WatchThesis):
        return False
    return update.pnl_ratio is None or update.pnl_ratio < -config.max_postpone_loss


def _catalyst_rejection(
    update: ThesisUpdate, today: date, calendar: TradingCalendar, config: HeartbeatConfig
) -> ThesisDatesRejection | None:
    """催化剂的检查，spec「检查的顺序」第 5 到 10 项。

    「催化剂到期以后」的表合成两个判断：保留（原样或只关联事件）在续期遇到到期时不算数；到期以后
    改了的必须是新的。续期遇到到期时，保留的催化剂截止日期没变，一定不是新的。
    """
    catalyst = update.thesis.catalyst
    if update.previous is not None:
        old = update.previous.catalyst
        # Pydantic 模型的 == 比较全部字段，引用的种类和 ID 都在里面
        kept = catalyst == old or _links_event(catalyst, old)
        expired = old.deadline <= today
        if kept and not (expired and update.renewal):
            return None  # 原样保留或只关联事件，不检查截止日期
        if expired and not _is_new(catalyst, old):
            return ThesisDatesRejection.STALE_CATALYST
    return _deadline_rejection(catalyst, update.schedule_day, today, calendar, config)


def _links_event(new: Catalyst, old: Catalyst) -> bool:
    """只关联事件：描述和截止日期不变，原来没有引用，现在引用一个事件。"""
    return (
        new.description == old.description
        and new.deadline == old.deadline
        and old.reference is None
        and new.reference is not None
        and new.reference.kind is ReferenceKind.EVENT
    )


def _is_new(new: Catalyst, old: Catalyst) -> bool:
    """新的催化剂：截止日期变了，描述或引用也至少变了一项。只改截止日期不算，否则到期未兑现的
    催化剂改个日期就能一直拖下去。
    """
    return new.deadline != old.deadline and (
        new.description != old.description or new.reference != old.reference
    )


def _deadline_rejection(
    catalyst: Catalyst,
    schedule_day: date | None,
    today: date,
    calendar: TradingCalendar,
    config: HeartbeatConfig,
) -> ThesisDatesRejection | None:
    """要检查的催化剂的截止日期，spec「催化剂的截止日期」。

    代码分不出催化剂有没有固定日期：引用日程项的按有固定日期检查，截止日期跟着日程项走；其余
    都按没有固定日期检查，查上限。和复查日一样先比较范围、再查交易日。
    """
    deadline = catalyst.deadline
    if deadline <= today:
        return ThesisDatesRejection.DEADLINE_TOO_SOON
    reference = catalyst.reference
    if reference is not None and reference.kind is ReferenceKind.SCHEDULE:
        if schedule_day is None:
            return ThesisDatesRejection.UNKNOWN_SCHEDULE
        # 第 1 和第 2 个交易日之间隔着周末时，周末在范围内，由下面给出"不是交易日"
        first = calendar.add_sessions(schedule_day, 1).day
        last = calendar.add_sessions(schedule_day, config.max_schedule_lag_sessions).day
        if not first <= deadline <= last:
            return ThesisDatesRejection.OFF_SCHEDULE
    elif deadline > calendar.add_sessions(today, config.max_deadline_sessions).day:
        return ThesisDatesRejection.DEADLINE_TOO_LATE
    if not calendar.is_session(deadline):
        return ThesisDatesRejection.DEADLINE_NOT_SESSION
    return None
