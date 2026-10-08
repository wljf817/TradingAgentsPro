"""/plan 填的内容：解析成交易方案，以及每种回复的文字。不做 I/O。

见 manual-plans spec「字段的解析」「回复」，以及 add-manual-plan-entry 的 design D2、D3、D10。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.integrations.discord.messages import ACTION_NAMES
from tradingagentspro.shared.trading_calendar import CalendarRangeError, TradingCalendar

# 数字只有一种写法：数字，可以带一个小数点和小数部分；百分数后面可以带一个 %
_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?")
_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?)%?")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_HUNDRED = Decimal(100)

# 构造方案时 Pydantic 报错的字段，在原因里写中文名
_FIELD_NAMES = {
    "plan_id": "方案 ID",
    "symbol": "股票",
    "sector": "板块",
    "action": "动作",
    "quantity": "股数",
    "order_type": "订单类型",
    "limit_price": "限价",
    "stop_price": "止损价",
    "emergency_threshold": "紧急阈值",
    "expires_at": "有效期",
    "max_price_deviation": "允许价格偏离",
}


@dataclass(frozen=True, slots=True)
class PlanEntry:
    """一条 /plan 里人填的内容：Discord 给的原样文字和整数，没填的是 None。"""

    action: TradeAction
    symbol: str
    sector: str | None = None
    quantity: int | None = None
    limit: str | None = None
    stop: str | None = None
    emergency: str | None = None
    deviation: str | None = None
    expires: str | None = None


class PlanFormatError(ValueError):
    """填的内容构不成方案。reasons 是全部原因，每条写明字段和填的文字。"""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("；".join(reasons))
        self.reasons = tuple(reasons)


def normalize_symbol(text: str) -> str:
    """股票代码：去掉首尾空白，转成大写。"""
    return text.strip().upper()


def build_plan(
    entry: PlanEntry, plan_id: UUID, sector: str, calendar: TradingCalendar
) -> TradePlan:
    """逐个字段解析，再构造方案；不通过时抛 PlanFormatError，一次列出全部原因。

    sector 是方案的板块：买入和加仓是填的，减仓和清仓是持仓的。
    """
    reasons: list[str] = []
    symbol = normalize_symbol(entry.symbol)
    if not symbol:
        reasons.append("股票不能为空")
    sector = sector.strip()
    if not sector:
        reasons.append("板块不能为空")
    limit = _price("限价", entry.limit, reasons)
    stop = _price("止损价", entry.stop, reasons)
    emergency = _percent("紧急阈值", entry.emergency, reasons)
    deviation = _percent("允许价格偏离", entry.deviation, reasons)
    expires_at = _expiry(entry.expires, calendar, reasons)
    if reasons:
        raise PlanFormatError(reasons)

    if entry.action.is_opening or entry.limit is not None:
        order_type = OrderType.LIMIT
    else:
        order_type = OrderType.MARKET
    try:
        return TradePlan(
            plan_id=plan_id,
            symbol=symbol,
            sector=sector,
            action=entry.action,
            quantity=entry.quantity,
            order_type=order_type,
            limit_price=limit,
            stop_price=stop,
            emergency_threshold=emergency,
            expires_at=expires_at,
            max_price_deviation=deviation,
        )
    except ValidationError as error:
        raise PlanFormatError([_reason(e) for e in error.errors()]) from error


def refusal_reply(user_id: int) -> str:
    """别人运行 /plan 时的提示。"""
    return f"只有 <@{user_id}> 可以录入方案"


def format_error_reply(error: PlanFormatError) -> str:
    """格式错误，每条原因一行。"""
    return "\n".join(["方案格式不对，没有提交：", *(f"- {r}" for r in error.reasons)])


def no_position_reply(action: TradeAction, symbol: str) -> str:
    """减仓或清仓的股票没有持仓。"""
    return f"没有 {symbol} 的持仓，不能{ACTION_NAMES[action]}；没有提交"


def lookup_error_reply(error: Exception) -> str:
    """查持仓出错：没有提交。"""
    return f"查持仓出错：{error}；没有提交，可以再录入一次"


def decision_reply(plan: TradePlan, decision: RiskDecision, channel_id: int) -> str:
    """风控结果：通过、调整或拒绝，见 spec「回复」的表。"""
    action = f"{ACTION_NAMES[plan.action]} {plan.symbol}"
    confirm = f"请在 <#{channel_id}> 的确认消息上确认"
    if decision.outcome is RiskOutcome.REJECTED:
        if plan.quantity is not None:
            action += f" {plan.quantity} 股"
        lines = [f"风控拒绝：{action}，不会下单"]
        lines.extend(f"- {v.rule}：{v.message}" for v in decision.violations)
    elif decision.outcome is RiskOutcome.ADJUSTED:
        if plan.quantity is not None:
            change = f"{plan.quantity} 股 → {decision.quantity} 股"
        else:
            change = f"改为 {decision.quantity} 股"
        lines = [f"风控调整：{action} {change}，原因见确认消息", confirm]
    else:
        lines = [f"风控通过：{action} {decision.quantity} 股", confirm]
    lines.append(f"方案 ID {plan.plan_id}")
    return "\n".join(lines)


def submit_error_reply(plan_id: UUID, error: Exception) -> str:
    """提交接口抛错：方案可能已经提交。"""
    return f"提交出错：{error}\n{_maybe_submitted(plan_id)}"


def timeout_reply(plan_id: UUID, timeout: timedelta) -> str:
    """提交接口超时：方案可能已经提交。"""
    return f"超过 {timeout.total_seconds():g} 秒没有得到风控结果\n{_maybe_submitted(plan_id)}"


def _maybe_submitted(plan_id: UUID) -> str:
    return (
        f"方案 ID {plan_id} 可能已经提交：频道里出现这个方案的确认消息就照常确认或拒绝，"
        "没有出现再重新录入"
    )


def _price(name: str, text: str | None, reasons: list[str]) -> Decimal | None:
    """价格：照写的位数转成 Decimal；没填是 None。"""
    if text is None:
        return None
    if not _NUMBER.fullmatch(text.strip()):
        reasons.append(f"{name}「{text}」不是数字，写成 180.50 这样")
        return None
    price = Decimal(text.strip())
    if price <= 0:
        reasons.append(f"{name}「{text}」必须大于 0")
        return None
    return price


def _percent(name: str, text: str | None, reasons: list[str]) -> Decimal | None:
    """百分数：8 和 8% 都是 0.08；没填是 None。"""
    if text is None:
        return None
    match = _PERCENT.fullmatch(text.strip())
    number = Decimal(match.group(1)) if match else None
    if number is None or not 0 < number < _HUNDRED:
        reasons.append(f"{name}「{text}」要写大于 0、小于 100 的百分数，比如 8 或 8%")
        return None
    return number.scaleb(-2)


def _expiry(text: str | None, calendar: TradingCalendar, reasons: list[str]) -> datetime | None:
    """有效期：到这个交易日收盘（UTC）；没填是 None。"""
    if text is None:
        return None
    try:
        if not _DAY.fullmatch(text.strip()):
            raise ValueError(text)
        day = date.fromisoformat(text.strip())
    except ValueError:
        reasons.append(f"有效期「{text}」要写成 年-月-日 的交易日，比如 2026-10-16")
        return None
    try:
        session = calendar.session(day)
    except CalendarRangeError:
        reasons.append(f"有效期 {day} 超出交易日历的范围")
        return None
    if session is None:
        reasons.append(f"有效期 {day} 不是交易日")
        return None
    return session.close


def _reason(error: ErrorDetails) -> str:
    """把 Pydantic 的一条错误转成原因：我们自己写的规则原样用，别的前面加字段的中文名。"""
    if error["type"] == "value_error" and "ctx" in error:
        return str(error["ctx"]["error"])
    location = error["loc"]
    name = _FIELD_NAMES.get(str(location[0]), str(location[0])) if location else ""
    return f"{name}：{error['msg']}" if name else str(error["msg"])
