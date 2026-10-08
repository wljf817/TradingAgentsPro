"""/plan 填的内容的解析和回复的文字。场景见 openspec 的 manual-plans spec。

不做 I/O：交易日历用样例日历，方案 ID 用 UUID(int=1)（场景里的 U1）。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from tests.fakes.calendar import sample_nyse_calendar
from tests.modules.notification.builders import CHANNEL_ID, USER_ID
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.integrations.discord.plans import (
    PlanEntry,
    PlanFormatError,
    build_plan,
    decision_reply,
    format_error_reply,
    lookup_error_reply,
    no_position_reply,
    refusal_reply,
    submit_error_reply,
    timeout_reply,
)

BUY, ADD, REDUCE, CLOSE = TradeAction.BUY, TradeAction.ADD, TradeAction.REDUCE, TradeAction.CLOSE
U1 = UUID(int=1)
CALENDAR = sample_nyse_calendar()
EXPIRES_AT = datetime(2026, 10, 14, 20, tzinfo=UTC)


def _buy(**changes: Any) -> PlanEntry:
    """/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171，changes 覆盖它。"""
    fields: dict[str, Any] = {
        "action": BUY,
        "symbol": "AAPL",
        "sector": "科技",
        "quantity": 100,
        "limit": "180.50",
        "stop": "171",
    }
    return PlanEntry(**(fields | changes))


def _build(entry: PlanEntry, sector: str | None = None) -> TradePlan:
    """买入和加仓用填的板块；减仓和清仓用传入的持仓板块。"""
    if sector is None:
        sector = entry.sector or ""
    return build_plan(entry, U1, sector, CALENDAR)


def _reasons(entry: PlanEntry, sector: str | None = None) -> tuple[str, ...]:
    with pytest.raises(PlanFormatError) as raised:
        _build(entry, sector)
    return raised.value.reasons


# 字段的解析


def test_full_buy() -> None:
    """spec「填全了的买入」。"""
    entry = _buy(symbol="aapl", emergency="8", deviation="5%", expires="2026-10-16")

    assert _build(entry) == TradePlan(
        plan_id=U1,
        symbol="AAPL",
        sector="科技",
        action=BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("180.50"),
        stop_price=Decimal(171),
        emergency_threshold=Decimal("0.08"),
        max_price_deviation=Decimal("0.05"),
        expires_at=datetime(2026, 10, 16, 20, tzinfo=UTC),
    )


def test_only_required_fields() -> None:
    """spec「只填必填项」。"""
    plan = _build(_buy())

    assert (plan.emergency_threshold, plan.max_price_deviation, plan.expires_at) == (
        None,
        None,
        None,
    )


def test_add() -> None:
    """spec「加仓」。"""
    entry = PlanEntry(
        action=ADD,
        symbol="MSFT",
        sector="科技",
        quantity=200,
        limit="410",
        stop="395",
        deviation="5",
    )

    plan = _build(entry)

    assert (plan.action, plan.symbol, plan.quantity, plan.order_type) == (
        ADD,
        "MSFT",
        200,
        OrderType.LIMIT,
    )
    assert (plan.limit_price, plan.stop_price) == (Decimal(410), Decimal(395))
    assert plan.max_price_deviation == Decimal("0.05")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("180.50", "180.50"), (" 180.5 ", "180.5"), ("0.9999", "0.9999")],
)
def test_price_spellings(text: str, expected: str) -> None:
    """spec「价格的写法」：照写的位数转成 Decimal。"""
    plan = _build(_buy(limit=text, stop="0.5"))

    assert plan.limit_price is not None
    assert str(plan.limit_price) == expected


@pytest.mark.parametrize("text", ["18o.5", "$180", "1,000", "1e3", "-5", ".5", ""])
def test_price_that_is_not_a_number(text: str) -> None:
    """spec「价格的写法」：不是数字，原因里带上填的文字。"""
    [reason] = _reasons(_buy(limit=text))

    assert "限价" in reason
    assert f"「{text}」" in reason
    assert "不是数字" in reason


@pytest.mark.parametrize("text", ["0", "0.00"])
def test_price_not_above_zero(text: str) -> None:
    """spec「价格不大于 0」。"""
    [reason] = _reasons(_buy(stop=text))

    assert "止损价" in reason
    assert "必须大于 0" in reason


@pytest.mark.parametrize(
    ("text", "expected"),
    [("8", "0.08"), ("8%", "0.08"), ("7.5", "0.075"), ("99.99", "0.9999"), ("0.08", "0.0008")],
)
def test_percentages(text: str, expected: str) -> None:
    """spec「百分数」：按百分数理解，0.08 是 0.0008。"""
    plan = _build(_buy(emergency=text))

    assert plan.emergency_threshold == Decimal(expected)


@pytest.mark.parametrize("text", ["0", "100", "120%", "8%%", "abc"])
def test_bad_percentages(text: str) -> None:
    """spec「百分数不对」。"""
    [reason] = _reasons(_buy(deviation=text))

    assert "允许价格偏离" in reason
    assert f"「{text}」" in reason
    assert "大于 0、小于 100 的百分数" in reason


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-16", datetime(2026, 10, 16, 20, tzinfo=UTC)),
        ("2026-11-02", datetime(2026, 11, 2, 21, tzinfo=UTC)),
        ("2026-11-27", datetime(2026, 11, 27, 18, tzinfo=UTC)),
    ],
    ids=["周五", "夏令时结束后", "提前收盘"],
)
def test_expiry(text: str, expected: datetime) -> None:
    """spec「有效期」：到这个交易日收盘，测试进程在 UTC+14 也一样。"""
    plan = _build(_buy(expires=text))

    assert plan.expires_at == expected


@pytest.mark.parametrize("text", ["2026-10-17", "2026-11-26"], ids=["周六", "感恩节"])
def test_expiry_not_a_trading_day(text: str) -> None:
    """spec「有效期不是交易日」。"""
    [reason] = _reasons(_buy(expires=text))

    assert text in reason
    assert "不是交易日" in reason


@pytest.mark.parametrize("text", ["10/16", "20261016", "2026-02-30"])
def test_expiry_spelled_wrong(text: str) -> None:
    """spec「有效期写法不对」。"""
    [reason] = _reasons(_buy(expires=text))

    assert f"「{text}」" in reason
    assert "年-月-日" in reason


def test_expiry_outside_the_calendar() -> None:
    """spec「有效期超出日历」：样例日历到 2027-01-29 为止。"""
    [reason] = _reasons(_buy(expires="2027-03-01"))

    assert "超出交易日历的范围" in reason


def test_blanks_around_symbol_and_sector() -> None:
    """spec「股票代码和板块的空白」。"""
    plan = _build(_buy(symbol=" brk.b ", sector=" 金融 ", limit="450", stop="430"))

    assert (plan.symbol, plan.sector) == ("BRK.B", "金融")


@pytest.mark.parametrize(("field", "name"), [("symbol", "股票"), ("sector", "板块")])
def test_blank_symbol_or_sector(field: str, name: str) -> None:
    """spec「股票代码和板块的空白」：只有空格。"""
    [reason] = _reasons(_buy(**{field: "   "}))

    assert f"{name}不能为空" in reason


def test_all_errors_are_listed_together() -> None:
    """spec「多处错误一起列出」。"""
    reasons = _reasons(_buy(limit="abc", stop="0", emergency="100"))

    assert len(reasons) == 3
    assert "限价" in reasons[0] and "不是数字" in reasons[0]
    assert "止损价" in reasons[1] and "必须大于 0" in reasons[1]
    assert "紧急阈值" in reasons[2] and "小于 100" in reasons[2]


def test_price_off_the_tick() -> None:
    """spec「不符合最小变动单位」：构造方案时的原因也列出。"""
    [reason] = _reasons(_buy(limit="180.505"))

    assert "限价 180.505 不符合最小变动单位" in reason


def test_quantity_below_one_that_bypassed_discord() -> None:
    """Discord 检查股数最小 1；万一绕过，构造方案时挡住，原因写字段的中文名。"""
    [reason] = _reasons(_buy(quantity=0))

    assert reason.startswith("股数：")


# 减仓和清仓的板块


def test_market_reduce_uses_the_held_sector() -> None:
    """spec「市价减仓」：填了股数、没填限价。"""
    plan = _build(PlanEntry(action=REDUCE, symbol="aapl", quantity=60), sector="科技")

    assert plan == TradePlan(
        plan_id=U1,
        symbol="AAPL",
        sector="科技",
        action=REDUCE,
        quantity=60,
        order_type=OrderType.MARKET,
    )


def test_limit_close_uses_the_held_sector() -> None:
    """spec「限价清仓」。"""
    entry = PlanEntry(action=CLOSE, symbol="AAPL", limit="185", expires="2026-10-16")

    plan = _build(entry, sector="科技")

    assert plan == TradePlan(
        plan_id=U1,
        symbol="AAPL",
        sector="科技",
        action=CLOSE,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(185),
        expires_at=datetime(2026, 10, 16, 20, tzinfo=UTC),
    )


# 回复的文字


def _decision(
    outcome: RiskOutcome, quantity: int | None, *violations: tuple[str, str]
) -> RiskDecision:
    return RiskDecision(
        outcome=outcome,
        quantity=quantity,
        emergency_threshold=None,
        expires_at=None if outcome is RiskOutcome.REJECTED else EXPIRES_AT,
        violations=tuple(RiskViolation(rule=r, message=m) for r, m in violations),
    )


def test_passed_reply() -> None:
    """spec「风控通过」。"""
    text = decision_reply(_build(_buy()), _decision(RiskOutcome.PASSED, 100), CHANNEL_ID)

    assert text.splitlines() == [
        "风控通过：买入 AAPL 100 股",
        f"请在 <#{CHANNEL_ID}> 的确认消息上确认",
        f"方案 ID {U1}",
    ]


def test_adjusted_reply() -> None:
    """spec「风控调整」。"""
    entry = PlanEntry(
        action=ADD, symbol="MSFT", sector="科技", quantity=200, limit="410", stop="395"
    )
    decision = _decision(RiskOutcome.ADJUSTED, 120, ("单笔计划风险", "超过上限"))

    text = decision_reply(_build(entry), decision, CHANNEL_ID)

    assert text.splitlines() == [
        "风控调整：加仓 MSFT 200 股 → 120 股，原因见确认消息",
        f"请在 <#{CHANNEL_ID}> 的确认消息上确认",
        f"方案 ID {U1}",
    ]


def test_rejected_reply() -> None:
    """spec「风控拒绝」：每条规则一行。"""
    decision = _decision(
        RiskOutcome.REJECTED,
        None,
        ("降级模式", "系统处于降级模式，暂停开仓"),
        ("单笔计划风险", "AAPL 的计划风险将是 3,040.00，超过上限 2,000.00"),
    )

    text = decision_reply(_build(_buy()), decision, CHANNEL_ID)

    assert text.splitlines() == [
        "风控拒绝：买入 AAPL 100 股，不会下单",
        "- 降级模式：系统处于降级模式，暂停开仓",
        "- 单笔计划风险：AAPL 的计划风险将是 3,040.00，超过上限 2,000.00",
        f"方案 ID {U1}",
    ]


def test_close_quantity_comes_from_the_decision() -> None:
    """spec「清仓的股数取风控结果」。"""
    plan = _build(PlanEntry(action=CLOSE, symbol="AAPL"), sector="科技")

    passed = decision_reply(plan, _decision(RiskOutcome.PASSED, 100), CHANNEL_ID)
    rejected = decision_reply(
        plan,
        _decision(RiskOutcome.REJECTED, None, ("可卖数量", "AAPL 的可卖数量是 0")),
        CHANNEL_ID,
    )

    assert passed.splitlines()[0] == "风控通过：清仓 AAPL 100 股"
    assert rejected.splitlines()[:2] == [
        "风控拒绝：清仓 AAPL，不会下单",
        "- 可卖数量：AAPL 的可卖数量是 0",
    ]


def test_adjusted_close_reply() -> None:
    """spec「回复」的表：清仓被调整时只写调整后的股数。"""
    plan = _build(PlanEntry(action=CLOSE, symbol="AAPL"), sector="科技")

    text = decision_reply(plan, _decision(RiskOutcome.ADJUSTED, 80), CHANNEL_ID)

    assert text.splitlines()[0] == "风控调整：清仓 AAPL 改为 80 股，原因见确认消息"


def test_format_error_reply() -> None:
    """spec「回复」的表：格式错误，每条原因一行。"""
    text = format_error_reply(PlanFormatError(["限价「abc」不是数字", "止损价「0」必须大于 0"]))

    assert text.splitlines() == [
        "方案格式不对，没有提交：",
        "- 限价「abc」不是数字",
        "- 止损价「0」必须大于 0",
    ]


def test_no_position_reply() -> None:
    """spec「没有持仓」。"""
    assert no_position_reply(CLOSE, "MSFT") == "没有 MSFT 的持仓，不能清仓；没有提交"


def test_lookup_error_reply() -> None:
    """spec「查持仓出错」。"""
    text = lookup_error_reply(RuntimeError("数据库暂时连不上"))

    assert text == "查持仓出错：数据库暂时连不上；没有提交，可以再录入一次"


def test_submit_error_reply() -> None:
    """spec「提交接口抛错」：方案可能已经提交。"""
    text = submit_error_reply(U1, RuntimeError("数据库暂时连不上"))

    assert text.splitlines() == [
        "提交出错：数据库暂时连不上",
        f"方案 ID {U1} 可能已经提交：频道里出现这个方案的确认消息就照常确认或拒绝，"
        "没有出现再重新录入",
    ]


def test_timeout_reply() -> None:
    """spec「提交超时」。"""
    text = timeout_reply(U1, timedelta(seconds=60))

    assert text.splitlines()[0] == "超过 60 秒没有得到风控结果"
    assert f"方案 ID {U1} 可能已经提交" in text


def test_refusal_reply() -> None:
    """spec「别人运行命令」。"""
    assert refusal_reply(USER_ID) == f"只有 <@{USER_ID}> 可以录入方案"
