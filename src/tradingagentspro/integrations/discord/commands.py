"""处理 /plan：只接受本人，先应答，再在工作线程里查持仓、解析、提交，最后回复。

见 manual-plans spec，以及 add-manual-plan-entry 的 design D8、D9。discord.py 的部分在
gateway.py：它把每条命令翻译成 CommandCall 和 PlanEntry 交给 PlanCommand。
"""

import asyncio
import logging
from typing import Protocol
from uuid import UUID

from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.integrations.discord.config import DiscordConfig
from tradingagentspro.integrations.discord.plans import (
    PlanEntry,
    PlanFormatError,
    build_plan,
    decision_reply,
    format_error_reply,
    lookup_error_reply,
    no_position_reply,
    normalize_symbol,
    refusal_reply,
    submit_error_reply,
    timeout_reply,
)
from tradingagentspro.modules.notification import PlanIntake
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar

_log = logging.getLogger(__name__)

_OUTCOMES = {
    RiskOutcome.PASSED: "风控通过",
    RiskOutcome.ADJUSTED: "风控调整",
    RiskOutcome.REJECTED: "风控拒绝",
}


class CommandCall(Protocol):
    """一次 /plan：discord.Interaction 里适配器用到的部分。回复都只有本人看得到。"""

    @property
    def user_id(self) -> int: ...

    async def refuse(self, text: str) -> None:
        """立即回复，这就是应答；用来拒绝别人。"""
        ...

    async def defer(self) -> None:
        """延迟应答：Discord 只对本人显示「正在思考」。"""
        ...

    async def reply(self, text: str) -> None:
        """把「正在思考」换成这段文字；应答后 15 分钟内有效。"""
        ...


class PlanCommand:
    """处理 /plan，见 manual-plans spec。

    handle 在 discord 线程的事件循环上运行；查持仓、解析和提交放到工作线程里，
    提交接口再慢也不卡住按钮和告警。
    """

    def __init__(
        self,
        intake: PlanIntake,
        ids: IdGenerator,
        calendar: TradingCalendar,
        config: DiscordConfig,
    ) -> None:
        self._intake = intake
        self._ids = ids
        self._calendar = calendar
        self._config = config

    async def handle(self, call: CommandCall, entry: PlanEntry) -> None:
        """处理一条命令，见 spec「应答和处理的先后」。不抛错：出错都写日志。"""
        head = f"/plan {entry.action.value}：用户 {call.user_id}"
        if call.user_id != self._config.user_id:
            _log.warning("%s，不是配置的用户，已拒绝", head)
            try:
                await call.refuse(refusal_reply(self._config.user_id))
            except Exception:
                _log.exception("%s，拒绝的提示没有发出去", head)
            return
        try:
            await call.defer()
        except Exception:
            _log.exception("%s，应答 Discord 失败，没有提交", head)
            return

        plan_id = self._ids.new_id()
        head += f"，方案 {plan_id}"
        timeout = self._config.submit_timeout
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(self._process, entry, plan_id, head), timeout.total_seconds()
            )
        except TimeoutError:
            # 工作线程里的提交接着跑，结果由 _process 写进日志
            _log.warning("%s，超过 %g 秒没有得到风控结果", head, timeout.total_seconds())
            text = timeout_reply(plan_id, timeout)
        try:
            await call.reply(text)
        except Exception:
            _log.exception("%s，回复没有发出去：%s", head, text)

    def _process(self, entry: PlanEntry, plan_id: UUID, head: str) -> str:
        """在工作线程里运行：查持仓、解析、提交，写结果日志，返回回复的文字。不抛错。"""
        if entry.action.is_opening:
            sector = entry.sector or ""
        else:
            symbol = normalize_symbol(entry.symbol)
            if not symbol:
                error = PlanFormatError(["股票不能为空"])
                _log.info("%s，格式错误：%s", head, error)
                return format_error_reply(error)
            try:
                held = self._intake.held_sector(symbol)
            except Exception as error:
                _log.exception("%s，查持仓出错，没有提交", head)
                return lookup_error_reply(error)
            if held is None:
                _log.info("%s，没有 %s 的持仓，没有提交", head, symbol)
                return no_position_reply(entry.action, symbol)
            sector = held

        try:
            plan = build_plan(entry, plan_id, sector, self._calendar)
        except PlanFormatError as error:
            _log.info("%s，格式错误：%s", head, error)
            return format_error_reply(error)
        try:
            decision = self._intake.submit(plan)
        except Exception as error:
            _log.exception("%s，提交出错", head)
            return submit_error_reply(plan_id, error)
        _log.info("%s，%s，%s 股", head, _OUTCOMES[decision.outcome], decision.quantity)
        return decision_reply(plan, decision, self._config.channel_id)
