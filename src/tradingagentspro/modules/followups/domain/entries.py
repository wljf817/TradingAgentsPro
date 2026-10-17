"""跟进的动作写进程序日志时的说明，见 program-log spec「调度器的动作」。

说明是跟进的内容（股票、原因类型、引用的对象、触发时间、问题和可能采取的动作）；拒绝时前面加
不通过的原因，作废时前面加之前和之后的状态。
"""

from datetime import UTC
from typing import Final

from tradingagentspro.contracts.conclusion import FollowUpReason, FollowUpRequest
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import (
    CatalystReference,
    Comparison,
    Condition,
    ConditionKind,
    ReferenceKind,
)
from tradingagentspro.modules.followups.domain.follow_up import FollowUp
from tradingagentspro.modules.followups.domain.rules import FollowUpRejection

_REASONS: Final = {
    FollowUpReason.EVENT_REACTION: "事件后反应",
    FollowUpReason.EXTERNAL_SCHEDULE: "外部日程",
    FollowUpReason.OBSERVATION: "价格或指标观察",
}
_REFERENCE_KINDS: Final = {ReferenceKind.SCHEDULE: "日程项", ReferenceKind.EVENT: "事件"}
_CONDITION_KINDS: Final = {
    ConditionKind.PRICE: "价格",
    ConditionKind.CHANGE: "当日涨跌幅",
    ConditionKind.VOLUME: "当日成交量",
}
_COMPARISONS: Final = {Comparison.AT_LEAST: "不低于", Comparison.AT_MOST: "不高于"}
# follow-ups spec「审批的顺序」的原因一栏
_REJECTIONS: Final = {
    FollowUpRejection.NO_REFERENCE: "没有引用具体对象：提示 PM 改为调整复查日",
    FollowUpRejection.REFERENCE_MISMATCH: "原因和引用的对象不符",
    FollowUpRejection.CATALYST_COVERED: "催化剂已经覆盖",
    FollowUpRejection.TRIGGER_PASSED: "触发时间已过",
    FollowUpRejection.TRIGGER_TOO_FAR: "触发时间太远",
    FollowUpRejection.STOCK_FULL: "待执行的跟进已满",
    FollowUpRejection.DAILY_LIMIT: "申请次数已用完",
}
_STATUSES: Final = {
    StockStatus.HOLDING: "持仓",
    StockStatus.WATCH: "关注",
    StockStatus.CANDIDATE: "候选",
}


def content(symbol: str, request: FollowUpRequest) -> str:
    """跟进的内容：股票、原因类型、引用的对象、触发时间（UTC）、问题和可能采取的动作。"""
    trigger_at = f"{request.trigger_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
    return (
        f"{symbol}，{_REASONS[request.reason]}，{_reference(request.reference)}，"
        f"触发时间 {trigger_at}；问题：{request.question}；"
        f"可能采取的动作：{request.possible_actions}"
    )


def rejection_detail(rejection: FollowUpRejection, symbol: str, request: FollowUpRequest) -> str:
    """拒绝跟进的说明：不通过的原因；申请的内容。"""
    return f"{_REJECTIONS[rejection]}；{content(symbol, request)}"


def void_detail(before: StockStatus | None, after: StockStatus | None, follow_up: FollowUp) -> str:
    """作废跟进的说明：之前和之后的状态；跟进的内容。"""
    change = f"{_status(before)}变为{_status(after)}"
    return f"{change}；{content(follow_up.symbol, follow_up.request)}"


def _reference(reference: CatalystReference | Condition | None) -> str:
    if reference is None:
        return "没有引用"
    if isinstance(reference, Condition):
        kind = _CONDITION_KINDS[reference.kind]
        return f"条件：{kind}{_COMPARISONS[reference.comparison]} {reference.value}"
    return f"{_REFERENCE_KINDS[reference.kind]} {reference.ref_id}"


def _status(status: StockStatus | None) -> str:
    """不在股票池也不是持仓时为 None。"""
    return "不在股票池" if status is None else _STATUSES[status]
