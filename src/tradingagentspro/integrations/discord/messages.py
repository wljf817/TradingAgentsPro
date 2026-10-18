"""消息内容、按钮、结果的嵌入，以及按钮 custom_id 的编码和解析。不做 I/O。

见 discord-notification spec「告警」「确认消息」「紧急退出的确认消息」，以及 add-discord-notify 的
design D5、D6；确认消息里案件的意见见 update-order-flow-case-plans 的 design D4。
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import discord

from tradingagentspro.contracts.conclusion import PlanDraft
from tradingagentspro.contracts.review import (
    CriticReview,
    PlanResponse,
    ResponseDecision,
    Verdict,
)
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction
from tradingagentspro.modules.notification import (
    CaseNote,
    ConfirmationOutcome,
    ConfirmationRequest,
    Disputed,
    EmergencyConfirmationRequest,
)

# Discord 一条消息最多 2000 字
MAX_CONTENT = 2000

# 动作的中文名，确认消息和 /plan 的回复共用
ACTION_NAMES = {
    TradeAction.BUY: "买入",
    TradeAction.ADD: "加仓",
    TradeAction.REDUCE: "减仓",
    TradeAction.CLOSE: "清仓",
}

_VERDICTS = {Verdict.APPROVE: "通过", Verdict.CHALLENGE: "质询", Verdict.VETO: "否决"}
_DECISIONS = {
    ResponseDecision.MAINTAIN: "维持",
    ResponseDecision.MODIFY: "修改",
    ResponseDecision.WITHDRAW: "撤回",
}

_OUTCOMES = {
    ConfirmationOutcome.CONFIRMED: ("已确认", discord.Colour.green()),
    ConfirmationOutcome.REJECTED: ("已拒绝", discord.Colour.red()),
    ConfirmationOutcome.TIMED_OUT: ("已超时", discord.Colour.light_grey()),
    ConfirmationOutcome.VOIDED: ("已作废", discord.Colour.light_grey()),
}

# 按钮的 custom_id：confirmation:<选择>:<方案 ID>:<截止时刻的秒数>，见 design D6
_BUTTON_PREFIX = "confirmation"
_BUTTON_ID = re.compile(
    rf"{_BUTTON_PREFIX}:(confirmed|rejected):([0-9a-f]{{8}}(?:-[0-9a-f]{{4}}){{3}}-[0-9a-f]{{12}}):([0-9]+)"
)


@dataclass(frozen=True, slots=True)
class ButtonChoice:
    """一个确认按钮代表的选择：哪个方案、确认还是拒绝、截止时刻（UTC，按秒取整）。"""

    plan_id: UUID
    outcome: ConfirmationOutcome
    deadline: datetime


def only_user(user_id: int) -> discord.AllowedMentions:
    """只允许通知这个用户：文字里的 @everyone、@here、角色和别人的 @ 都不通知。"""
    return discord.AllowedMentions(
        everyone=False, users=[discord.Object(id=user_id)], roles=False, replied_user=False
    )


def alert_content(text: str, user_id: int) -> str:
    """告警：@ 用户加上文字；超过 2000 字时把文字截短，最后一个字是「…」。"""
    return _fit(f"<@{user_id}> {text}")


def _fit(content: str) -> str:
    """超过 2000 字时从结尾截短，使整条正好 2000 字，最后一个字是「…」。"""
    if len(content) <= MAX_CONTENT:
        return content
    return content[: MAX_CONTENT - 1] + "…"


def confirmation_content(request: ConfirmationRequest, user_id: int) -> str:
    """确认消息的正文，见 spec「确认消息」的表。"""
    plan, decision = request.plan, request.decision
    quantity = decision.quantity
    lines = [f"<@{user_id}> 请确认：{ACTION_NAMES[plan.action]} {plan.symbol} {quantity} 股"]

    terms: list[str] = []
    if plan.order_type is OrderType.LIMIT and plan.limit_price is not None:
        amount = f"，金额 {quantity * plan.limit_price:,.2f}" if quantity is not None else ""
        terms.append(f"限价 {plan.limit_price}{amount}")
    else:
        terms.append("市价")
    if plan.stop_price is not None:
        terms.append(f"止损价 {plan.stop_price}")
    if decision.emergency_threshold is not None:
        terms.append(f"紧急阈值 {_percent(decision.emergency_threshold)}")
    if plan.max_price_deviation is not None:
        terms.append(f"允许价格偏离 {_percent(plan.max_price_deviation)}")
    lines.append("；".join(terms))

    sector = f"{plan.sector}板块"
    if decision.expires_at is not None:
        sector += f"；有效期至 {_timestamp(decision.expires_at, 'f')}"
    lines.append(sector)

    if decision.outcome is RiskOutcome.ADJUSTED:
        if plan.quantity is not None:
            lines.append(f"已被风控调整：{plan.quantity} 股 → {quantity} 股")
        else:
            lines.append(f"已被风控调整：改为 {quantity} 股")
        lines.extend(f"- {v.rule}：{v.message}" for v in decision.violations)

    lines.append(_deadline_line(request.deadline))
    lines.append(f"方案 ID {plan.plan_id}")
    if request.note is not None:
        lines.extend(_note_lines(request.note))
    return _fit("\n".join(lines))


def _note_lines(note: CaseNote) -> list[str]:
    """案件的意见，放在确认消息的最后，见 spec「确认消息」的第二张表。"""
    if not isinstance(note, Disputed):
        return ["Critic 不可用，方案没有经过审核"]
    lines = ["Critic 有分歧，双方的意见：", f"PM 的方案理由：{note.rationale}"]
    for i, review in enumerate(note.reviews):
        lines.extend(_review_lines(i + 1, review))
        if i < len(note.responses):
            lines.extend(_response_lines(i + 1, note.responses[i]))
    return lines


def _review_lines(n: int, review: CriticReview) -> list[str]:
    head = f"Critic 第 {n} 次审核：{_VERDICTS[review.verdict]}；{review.rationale}"
    return [head, *(f"- {c}" for c in review.challenges)]


def _response_lines(n: int, response: PlanResponse) -> list[str]:
    lines = [f"PM 第 {n} 轮回应：{_DECISIONS[response.decision]}"]
    lines.extend(f"- {r}" for r in response.replies)
    if response.plan is not None:
        lines.append(f"修改后：{_terms(response.plan)}")
    return lines


def _terms(terms: PlanDraft) -> str:
    """修改后的方案条款：股数、限价、止损价和紧急阈值（case-stages spec「Critic 审核和 PM 回应的
    格式」要求修改的条款都写这几项）。"""
    assert terms.emergency_threshold is not None  # PlanResponse 校验过修改的条款
    return (
        f"{terms.quantity} 股，限价 {terms.limit_price}，止损价 {terms.stop_price}，"
        f"紧急阈值 {_percent(terms.emergency_threshold)}"
    )


def emergency_confirmation_content(request: EmergencyConfirmationRequest, user_id: int) -> str:
    """紧急退出的确认消息的正文，见 spec「紧急退出的确认消息」的表。"""
    symbol = request.plan.symbol
    return "\n".join(
        [
            f"<@{user_id}> 请确认紧急退出：清仓 {symbol}",
            f"原因：{request.reason}",
            f"当前持仓 {request.quantity} 股",
            f"确认以后：作废 {symbol} 还没下单的方案，撤掉挂着的订单，"
            "订单都结束以后市价卖出全部可卖股数",
            f"拒绝或超时：今天不再紧急退出 {symbol}，止损单照挂",
            _deadline_line(request.deadline),
            f"方案 ID {request.plan.plan_id}",
        ]
    )


def _deadline_line(deadline: datetime) -> str:
    """确认消息里的截止时刻，两种确认消息共用。"""
    return f"请在 {_timestamp(deadline, 't')}（{_timestamp(deadline, 'R')}）前确认，过时按超时处理"


def confirmation_view(
    request: ConfirmationRequest | EmergencyConfirmationRequest,
) -> discord.ui.View:
    """确认消息的两个按钮：绿色的「确认」和红色的「拒绝」，两种确认消息相同。

    不在内存里等按钮，见 design D6。
    """
    plan_id, deadline = request.plan.plan_id, request.deadline
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.success,
            label="确认",
            custom_id=button_id(plan_id, ConfirmationOutcome.CONFIRMED, deadline),
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="拒绝",
            custom_id=button_id(plan_id, ConfirmationOutcome.REJECTED, deadline),
        )
    )
    return view


def outcome_embed(outcome: ConfirmationOutcome, at: datetime) -> discord.Embed:
    """确认的结果：标题和颜色，时刻是得出结果的时刻。"""
    title, colour = _OUTCOMES[outcome]
    return discord.Embed(title=title, colour=colour, timestamp=at)


def outcome_label(outcome: ConfirmationOutcome) -> str:
    """结果的名称，比如「已确认」。"""
    return _OUTCOMES[outcome][0]


def button_id(plan_id: UUID, outcome: ConfirmationOutcome, deadline: datetime) -> str:
    """按钮的 custom_id；截止时刻按秒向下取整。"""
    return f"{_BUTTON_PREFIX}:{outcome.value}:{plan_id}:{int(deadline.timestamp())}"


def parse_button_id(custom_id: str) -> ButtonChoice | None:
    """确认按钮的 custom_id 代表的选择；不是确认按钮的（别的按钮、斜杠命令）为 None。"""
    match = _BUTTON_ID.fullmatch(custom_id)
    if match is None:
        return None
    outcome, plan_id, seconds = match.groups()
    return ButtonChoice(
        plan_id=UUID(plan_id),
        outcome=ConfirmationOutcome(outcome),
        deadline=datetime.fromtimestamp(int(seconds), UTC),
    )


def _percent(ratio: Decimal) -> str:
    """0.08 是 8%，0.075 是 7.5%。"""
    return f"{(ratio * 100).normalize():f}%"


def _timestamp(moment: datetime, style: str) -> str:
    """Discord 的时间戳标记：每个人按自己设备的时区看到。"""
    return f"<t:{int(moment.timestamp())}:{style}>"
