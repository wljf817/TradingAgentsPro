"""用 Discord 实现通知模块的 Notifier：线程、同步调用、重复请求和按钮的处理。

discord.py 在自己的线程 discord 上跑自己的事件循环；同步的调用方把工作交给这个循环并等结果，
最多等发送超时。按钮按下时先在内存里判断、应答 Discord，再在工作线程里调用回调。
见 discord-notification spec 和 add-discord-notify 的 design D7。
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Protocol
from uuid import UUID

import discord

from tradingagentspro.integrations.discord.config import DiscordConfig
from tradingagentspro.integrations.discord.messages import (
    ButtonChoice,
    alert_content,
    confirmation_content,
    confirmation_view,
    only_user,
    outcome_embed,
    outcome_label,
    parse_button_id,
)
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    ConfirmationRequest,
    DecisionHandler,
    NotificationUnavailableError,
    PendingConfirmation,
)
from tradingagentspro.shared.clock import Clock

_log = logging.getLogger(__name__)

# 按钮的名称
_LABELS = {ConfirmationOutcome.CONFIRMED: "「确认」", ConfirmationOutcome.REJECTED: "「拒绝」"}


class ButtonPress(Protocol):
    """一次按钮按下：discord.Interaction 里适配器用到的部分。"""

    @property
    def user_id(self) -> int: ...

    @property
    def custom_id(self) -> str: ...

    @property
    def message_id(self) -> int: ...

    async def defer(self) -> None:
        """应答 Discord，暂不改消息。"""
        ...

    async def reply(self, text: str) -> None:
        """只有按的人看得到的回复；还没应答时，这就是应答。"""
        ...

    async def show(self, embed: discord.Embed) -> None:
        """去掉按钮、换上结果；还没应答时，这就是应答。"""
        ...


type PressHandler = Callable[[ButtonPress], Coroutine[Any, Any, None]]


class Gateway(Protocol):
    """适配器用到的 Discord 连接：DiscordGateway 是真实的实现，测试用替身。

    失败一律抛 NotificationUnavailableError。
    """

    async def run(self, on_press: PressHandler, ready: Callable[[], None]) -> None:
        """登录、连接、核对频道，然后调用 ready；一直运行到 close。"""
        ...

    async def send(
        self,
        content: str,
        view: discord.ui.View | None,
        mentions: discord.AllowedMentions,
    ) -> int:
        """往频道发一条消息，只通知 mentions 里允许的人，返回消息 ID。"""
        ...

    async def edit(self, message_id: int, embed: discord.Embed) -> None:
        """去掉这条消息的按钮、换上结果；消息已被删掉时什么也不做。"""
        ...

    async def close(self) -> None: ...


class DiscordNotifier:
    """用 Discord 实现 Notifier。三个同步方法可以从任何线程调用。

    先 start() 再用，退出时 close()。下面三项状态只在事件循环上读写，不需要锁：
    每个方案的确认请求、每条消息已有的结果、正在调用回调的消息。
    """

    def __init__(
        self,
        gateway: Gateway,
        config: DiscordConfig,
        clock: Clock,
        on_decision: DecisionHandler,
    ) -> None:
        self._gateway = gateway
        self._config = config
        self._clock = clock
        self._on_decision = on_decision
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._closing = False
        self._failure: BaseException | None = None
        self._requests: dict[UUID, asyncio.Task[PendingConfirmation]] = {}
        self._settled: dict[int, ConfirmationOutcome] = {}
        self._busy: set[int] = set()

    def start(self) -> None:
        """在线程 discord 上连接 Discord，连上并核对完频道才返回。

        失败（登录失败、频道不对）或超过连接超时时抛 NotificationUnavailableError。
        """
        if self._thread is not None:
            raise RuntimeError("DiscordNotifier 只能启动一次")
        started = threading.Event()  # 连上了，或者线程已经结束
        self._thread = threading.Thread(
            target=self._run, args=(started,), name="discord", daemon=True
        )
        self._thread.start()
        seconds = self._config.connect_timeout.total_seconds()
        if not started.wait(seconds):
            self.close()
            raise NotificationUnavailableError(f"Discord 超过 {seconds:g} 秒没有连上（连接超时）")
        if not self._ready.is_set():
            raise NotificationUnavailableError(f"Discord 启动失败：{self._failure}")

    def close(self) -> None:
        """断开连接，等线程结束（最多等发送超时）。"""
        self._closing = True
        loop, thread = self._loop, self._thread
        seconds = self._config.send_timeout.total_seconds()
        if loop is not None and not self._stopped.is_set():
            try:
                asyncio.run_coroutine_threadsafe(self._gateway.close(), loop).result(seconds)
            except Exception as error:
                _log.warning("关闭 Discord 连接时出错：%s", error)
        if thread is not None:
            thread.join(seconds)

    def alert(self, text: str) -> None:
        """推送一条告警：@ 配置的用户，只通知他。"""
        user_id = self._config.user_id
        self._call(
            lambda: self._gateway.send(alert_content(text, user_id), None, only_user(user_id))
        )
        _log.info("推送告警：%s", text)

    def request_confirmation(self, request: ConfirmationRequest) -> PendingConfirmation:
        """发出确认消息；同一个方案重复请求时返回第一次的那条，第一次失败了的重新发送。"""
        return self._call(lambda: self._request(request))

    def finish_confirmation(self, message_id: str, outcome: ConfirmationOutcome) -> None:
        """把确认消息改为调用方报告的结果、去掉按钮；之后按钮不再交给回调。"""
        self._call(lambda: self._finish(int(message_id), outcome))

    # 线程和同步调用

    def _run(self, started: threading.Event) -> None:
        try:
            asyncio.run(self._main(started))
            if not self._closing:
                self._failure = RuntimeError("连接结束了")
        except BaseException as error:
            self._failure = error
        finally:
            self._stopped.set()
            started.set()
        if self._ready.is_set() and not self._closing:
            _log.error("Discord 连接意外中止：%s", self._failure)

    async def _main(self, started: threading.Event) -> None:
        self._loop = asyncio.get_running_loop()

        def ready() -> None:
            self._ready.set()
            started.set()

        await self._gateway.run(self._on_press, ready)

    def _call[T](self, work: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """在事件循环上运行 work() 并等结果，最多等发送超时。"""
        loop = self._loop
        if loop is None or not self._ready.is_set():
            raise NotificationUnavailableError("Discord 还没启动")
        if self._stopped.is_set():
            if self._closing:
                raise NotificationUnavailableError("Discord 已经关闭")
            raise NotificationUnavailableError(f"Discord 连接已中止：{self._failure}")
        seconds = self._config.send_timeout.total_seconds()
        try:
            future = asyncio.run_coroutine_threadsafe(work(), loop)
        except RuntimeError as error:  # 事件循环刚刚关闭
            raise NotificationUnavailableError(f"Discord 连接已中止：{error}") from error
        try:
            return future.result(seconds)
        except TimeoutError:
            future.cancel()
            raise NotificationUnavailableError(f"Discord 超过 {seconds:g} 秒没有完成") from None
        except asyncio.CancelledError:
            raise NotificationUnavailableError("Discord 连接已中止") from None

    # 在事件循环上运行的部分

    async def _request(self, request: ConfirmationRequest) -> PendingConfirmation:
        plan_id = request.plan.plan_id
        task = self._requests.get(plan_id)
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            task = asyncio.create_task(self._send_confirmation(request))
            # 没人等它的结果时（调用方已经超时），也取走异常，免得 asyncio 报未取走的异常
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            self._requests[plan_id] = task
        # shield：调用方超时取消时，不取消发送本身，重试时拿到的是那条迟到的消息
        return await asyncio.shield(task)

    async def _send_confirmation(self, request: ConfirmationRequest) -> PendingConfirmation:
        user_id = self._config.user_id
        message_id = await self._gateway.send(
            confirmation_content(request, user_id), confirmation_view(request), only_user(user_id)
        )
        _log.info("请求确认：方案 %s，消息 %s", request.plan.plan_id, message_id)
        return PendingConfirmation(message_id=str(message_id), deadline=request.deadline)

    async def _finish(self, message_id: int, outcome: ConfirmationOutcome) -> None:
        self._settled[message_id] = outcome
        await self._gateway.edit(message_id, outcome_embed(outcome, self._clock.now()))
        _log.info("确认消息 %s 改为%s（调用方报告）", message_id, outcome)

    async def _on_press(self, press: ButtonPress) -> None:
        choice = parse_button_id(press.custom_id)
        if choice is None:
            return  # 不是确认消息的按钮：不应答，留给别的功能
        try:
            await self._handle(press, choice)
        except Exception:
            _log.exception("处理方案 %s 的按钮时出错", choice.plan_id)

    async def _handle(self, press: ButtonPress, choice: ButtonChoice) -> None:
        """按 spec「按钮的处理」的表处理一次按下。

        应答 Discord 之前只做内存里的判断，保证 3 秒内应答；回调在工作线程里运行。
        """
        message_id = press.message_id
        pressed = (
            f"按钮：用户 {press.user_id} 对方案 {choice.plan_id} 按了{_LABELS[choice.outcome]}"
        )
        if press.user_id != self._config.user_id:
            _log.warning("%s，不是配置的用户，已拒绝", pressed)
            await press.reply(f"只有 <@{self._config.user_id}> 可以确认或拒绝这个方案")
            return
        settled = self._settled.get(message_id)
        if settled is not None:
            _log.info("%s，这个方案已经%s", pressed, outcome_label(settled))
            await press.reply(f"这个方案{outcome_label(settled)}")
            return
        if message_id in self._busy:
            _log.info("%s，上一次按下还在处理", pressed)
            await press.reply("上一次操作还在处理，请稍等")
            return
        now = self._clock.now()
        if now >= choice.deadline:
            self._settled[message_id] = ConfirmationOutcome.TIMED_OUT
            _log.info("%s，已经过了截止时刻，显示已超时", pressed)
            await press.show(outcome_embed(ConfirmationOutcome.TIMED_OUT, now))
            return

        self._busy.add(message_id)
        try:
            await press.defer()
            try:
                await asyncio.to_thread(self._on_decision, choice.plan_id, choice.outcome)
            except Exception:
                _log.exception("%s，回调出错，没有送达", pressed)
                await press.reply("没有送达，请再按一次")
                return
        finally:
            self._busy.discard(message_id)
        if message_id in self._settled:
            # 回调运行期间调用方报告了结果：调用方的结果是最后显示的那个
            _log.info("%s，已送达；调用方已经报告了结果，不再改消息", pressed)
            return
        self._settled[message_id] = choice.outcome
        _log.info("%s，已送达", pressed)
        await press.show(outcome_embed(choice.outcome, self._clock.now()))
