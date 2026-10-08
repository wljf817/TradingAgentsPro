"""消息内容、按钮和结果的测试。场景见 openspec 的 discord-notification spec「告警」「确认消息」，
按钮的 custom_id 见 add-discord-notify 的 design D6。
"""

from datetime import UTC, datetime
from uuid import UUID

import discord
import pytest

from tests.modules.notification.builders import (
    DEADLINE,
    OTHER_USER_ID,
    P1_ID,
    USER_ID,
    p1_request,
    p2,
    p2_decision,
    p3,
    p3_decision,
    p4,
    p4_decision,
)
from tradingagentspro.integrations.discord.messages import (
    ButtonChoice,
    alert_content,
    button_id,
    confirmation_content,
    confirmation_view,
    only_user,
    outcome_embed,
    parse_button_id,
)
from tradingagentspro.modules.notification import ConfirmationOutcome, ConfirmationRequest

DEADLINE_EPOCH = 1791469800  # 2026-10-08 14:30Z
EXPIRES_EPOCH = 1791489600  # 2026-10-08 20:00Z

# 告警


def test_alert_mentions_the_user() -> None:
    """spec「推送告警」：开头 @ 配置的用户，后面是原样的文字。"""
    content = alert_content("AAPL 没有止损单：挂单失败", USER_ID)

    assert content == "<@1001> AAPL 没有止损单：挂单失败"


def test_only_the_user_can_be_notified() -> None:
    """spec「文字里有 @everyone」：文字原样保留，但只允许通知配置的用户。"""
    text = f"对账不一致 @everyone <@{OTHER_USER_ID}>"
    mentions = only_user(USER_ID)

    assert alert_content(text, USER_ID) == f"<@1001> {text}"
    assert mentions.everyone is False
    assert mentions.roles is False
    assert mentions.replied_user is False
    assert isinstance(mentions.users, list)
    assert [user.id for user in mentions.users] == [USER_ID]


def test_long_alert_is_cut_to_2000() -> None:
    """spec「文字太长」。"""
    content = alert_content("字" * 2500, USER_ID)

    assert len(content) == 2000
    assert content.startswith("<@1001> 字")
    assert content.endswith("字…")


def test_alert_of_exactly_2000_is_not_cut() -> None:
    """spec「告警」：正好 2000 字的不截短。"""
    text = "字" * (2000 - len("<@1001> "))

    assert alert_content(text, USER_ID) == f"<@1001> {text}"


# 确认消息


def test_buy_plan_that_passed() -> None:
    """spec「买入方案通过风控」。"""
    content = confirmation_content(p1_request(), USER_ID)

    assert content.splitlines() == [
        "<@1001> 请确认：买入 AAPL 100 股",
        "限价 180.50，金额 18,050.00；止损价 171.00；紧急阈值 8%",
        f"科技板块；有效期至 <t:{EXPIRES_EPOCH}:f>",
        f"请在 <t:{DEADLINE_EPOCH}:t>（<t:{DEADLINE_EPOCH}:R>）前确认，过时按超时处理",
        "方案 ID 0b7c2d4e-3f5a-4b6c-8d9e-0f1a2b3c4d5e",
    ]


def test_add_plan_adjusted_by_risk() -> None:
    """spec「加仓方案被风控调整」。"""
    request = ConfirmationRequest(plan=p2(), decision=p2_decision(), deadline=DEADLINE)

    lines = confirmation_content(request, USER_ID).splitlines()

    assert lines[0] == "<@1001> 请确认：加仓 MSFT 120 股"
    assert lines[1] == "限价 410.00，金额 49,200.00；止损价 395.00；紧急阈值 7.5%；允许价格偏离 5%"
    assert lines[3:6] == [
        "已被风控调整：200 股 → 120 股",
        "- 单笔计划风险：MSFT 的计划风险将是 3,040.00，超过上限 2,000.00",
        "- 组合合计风险：组合合计风险将是 9,000.00，超过上限 8,000.00",
    ]


def test_market_reduce_plan() -> None:
    """spec「市价减仓」。"""
    request = ConfirmationRequest(plan=p3(), decision=p3_decision(), deadline=DEADLINE)

    lines = confirmation_content(request, USER_ID).splitlines()

    assert lines[0] == "<@1001> 请确认：减仓 AAPL 60 股"
    assert lines[1] == "市价"
    text = "\n".join(lines)
    assert "金额" not in text
    assert "止损价" not in text
    assert "紧急阈值" not in text


def test_limit_close_plan() -> None:
    """spec「限价清仓」：清仓也写风控给出的股数。"""
    request = ConfirmationRequest(plan=p4(), decision=p4_decision(), deadline=DEADLINE)

    lines = confirmation_content(request, USER_ID).splitlines()

    assert lines[0] == "<@1001> 请确认：清仓 AAPL 100 股"
    assert lines[1] == "限价 185.00，金额 18,500.00"


def test_confirmation_buttons() -> None:
    """spec「买入方案通过风控」：消息带绿色的「确认」和红色的「拒绝」，按钮自带方案、选择和截止时刻。"""
    view = confirmation_view(p1_request())

    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    assert [(b.label, b.style, b.custom_id) for b in buttons] == [
        (
            "确认",
            discord.ButtonStyle.success,
            f"confirmation:confirmed:{P1_ID}:{DEADLINE_EPOCH}",
        ),
        (
            "拒绝",
            discord.ButtonStyle.danger,
            f"confirmation:rejected:{P1_ID}:{DEADLINE_EPOCH}",
        ),
    ]
    assert view.timeout is None


# 按钮的 custom_id


def test_button_id_round_trip() -> None:
    """design D6：编码以后解析回同样的方案、选择和截止时刻，不超过 100 字。"""
    custom_id = button_id(P1_ID, ConfirmationOutcome.CONFIRMED, DEADLINE)

    assert custom_id == f"confirmation:confirmed:{P1_ID}:{DEADLINE_EPOCH}"
    assert len(custom_id) <= 100
    assert parse_button_id(custom_id) == ButtonChoice(
        plan_id=P1_ID, outcome=ConfirmationOutcome.CONFIRMED, deadline=DEADLINE
    )


def test_deadline_is_rounded_down_to_the_second() -> None:
    """design D6：截止时刻按秒向下取整。"""
    deadline = datetime(2026, 10, 8, 14, 30, 0, 999999, tzinfo=UTC)

    choice = parse_button_id(button_id(P1_ID, ConfirmationOutcome.REJECTED, deadline))

    assert choice == ButtonChoice(
        plan_id=P1_ID, outcome=ConfirmationOutcome.REJECTED, deadline=DEADLINE
    )


@pytest.mark.parametrize(
    "custom_id",
    [
        "plan:new",
        f"other:confirmed:{P1_ID}:{DEADLINE_EPOCH}",
        f"confirmation:timed_out:{P1_ID}:{DEADLINE_EPOCH}",
        f"confirmation:confirmed:not-a-uuid:{DEADLINE_EPOCH}",
        f"confirmation:confirmed:{P1_ID}:soon",
        f"confirmation:confirmed:{P1_ID}:{DEADLINE_EPOCH}:extra",
        f"confirmation:confirmed:{UUID(int=0)}",
    ],
)
def test_foreign_button_ids_are_not_parsed(custom_id: str) -> None:
    """spec「别的按钮或命令」：不是确认按钮的 custom_id 解析不了。"""
    assert parse_button_id(custom_id) is None


# 结果


@pytest.mark.parametrize(
    ("outcome", "title", "colour"),
    [
        (ConfirmationOutcome.CONFIRMED, "已确认", discord.Colour.green()),
        (ConfirmationOutcome.REJECTED, "已拒绝", discord.Colour.red()),
        (ConfirmationOutcome.TIMED_OUT, "已超时", discord.Colour.light_grey()),
        (ConfirmationOutcome.VOIDED, "已作废", discord.Colour.light_grey()),
    ],
)
def test_outcome_embed(outcome: ConfirmationOutcome, title: str, colour: discord.Colour) -> None:
    """design D5：结果用嵌入显示，标题、颜色和得出结果的时刻。"""
    at = datetime(2026, 10, 8, 14, 5, tzinfo=UTC)

    embed = outcome_embed(outcome, at)

    assert embed.title == title
    assert embed.colour == colour
    assert embed.timestamp == at
