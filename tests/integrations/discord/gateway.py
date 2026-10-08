"""测试用的 Discord 连接和按钮按下：DiscordNotifier 的测试不连 Discord，用它们安排各种情况。

见 add-discord-notify 的 design D10。
"""

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count
from uuid import UUID

import discord

from tradingagentspro.integrations.discord.notifier import ButtonPress, PressHandler
from tradingagentspro.modules.notification import ConfirmationOutcome

WAIT_SECONDS = 5  # 测试里等别的线程的上限；正常情况下远用不到


@dataclass
class Sent:
    """发出的一条消息。"""

    message_id: int
    content: str
    view: discord.ui.View | None
    mentions: discord.AllowedMentions


@dataclass
class FakePress:
    """一次按钮按下：把每次调用按顺序记进 log，log 可以和回调共用，用来检查先后。

    shown 是 show 收到的结果，用来检查结果的时刻。
    """

    user_id: int
    custom_id: str
    message_id: int
    log: list[str] = field(default_factory=list)
    shown: list[discord.Embed] = field(default_factory=list)

    async def defer(self) -> None:
        self.log.append("defer")

    async def reply(self, text: str) -> None:
        self.log.append(f"reply:{text}")

    async def show(self, embed: discord.Embed) -> None:
        self.log.append(f"show:{embed.title}")
        self.shown.append(embed)


class FakeGateway:
    """满足 Gateway 的替身。

    - sent：发出的消息，消息 ID 从 101 开始；edits：改过的消息（消息 ID 和结果的标题）
    - deleted：已被删掉的消息 ID，改这些消息时什么也不做
    - run_error：设了的话，run 一开始就抛它（登录失败、频道不对）
    - never_ready：run 一直不调用 ready（连不上）
    - loop_thread：run 所在的线程，也就是事件循环所在的线程
    """

    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self.edits: list[tuple[int, str | None]] = []
        self.deleted: set[int] = set()
        self.run_error: Exception | None = None
        self.never_ready = False
        self.loop_thread: threading.Thread | None = None
        self._ids = count(101)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_press: PressHandler | None = None
        self._closed: asyncio.Event | None = None
        self._gate: asyncio.Event | None = None
        self._abort_error: Exception | None = None
        self._fail_next: Exception | None = None

    # Gateway

    async def run(self, on_press: PressHandler, ready: Callable[[], None]) -> None:
        self._loop = asyncio.get_running_loop()
        self.loop_thread = threading.current_thread()
        self._on_press = on_press
        self._closed = asyncio.Event()
        self._gate = asyncio.Event()
        self._gate.set()
        if self.run_error is not None:
            raise self.run_error
        if not self.never_ready:
            ready()
        await self._closed.wait()
        if self._abort_error is not None:
            raise self._abort_error

    async def send(
        self,
        content: str,
        view: discord.ui.View | None,
        mentions: discord.AllowedMentions,
    ) -> int:
        assert self._gate is not None
        await self._gate.wait()
        if self._fail_next is not None:
            error, self._fail_next = self._fail_next, None
            raise error
        message_id = next(self._ids)
        self.sent.append(Sent(message_id, content, view, mentions))
        return message_id

    async def edit(self, message_id: int, embed: discord.Embed) -> None:
        if message_id not in self.deleted:
            self.edits.append((message_id, embed.title))

    async def close(self) -> None:
        assert self._closed is not None
        self._closed.set()

    # 测试安排 Discord 那边的情况

    def hold_sends(self) -> None:
        """之后的发送一直等着，直到 release_sends。"""
        self._on_loop(lambda: self._gate.clear() if self._gate else None)

    def release_sends(self) -> None:
        self._on_loop(lambda: self._gate.set() if self._gate else None)

    def fail_next_send(self, error: Exception) -> None:
        """下一次发送抛 error。"""
        self._fail_next = error

    def abort(self, error: Exception) -> None:
        """连接因为不可恢复的错误中止：run 抛 error。"""
        self._abort_error = error
        self._on_loop(lambda: self._closed.set() if self._closed else None)

    def press(self, press: ButtonPress) -> None:
        """从测试线程把一次按下交给事件循环，等它处理完（包括回调）。"""
        assert self._loop is not None and self._on_press is not None
        handler = self._on_press
        asyncio.run_coroutine_threadsafe(handler(press), self._loop).result(WAIT_SECONDS)

    def _on_loop(self, action: Callable[[], object]) -> None:
        assert self._loop is not None

        async def run() -> None:
            action()

        asyncio.run_coroutine_threadsafe(run(), self._loop).result(WAIT_SECONDS)


class Decisions:
    """按钮的回调：记录收到的方案和选择，以及运行的线程。

    - log：和 FakePress 共用时，回调记一条 "callback:<选择>"
    - gate：设了的话，回调先等它打开（最多 WAIT_SECONDS 秒），用来让回调停住
    - fail_next：设为真时，下一次回调抛错，之后恢复正常
    """

    def __init__(self, log: list[str] | None = None) -> None:
        self.calls: list[tuple[UUID, ConfirmationOutcome]] = []
        self.threads: list[threading.Thread] = []
        self.log = log if log is not None else []
        self.gate: threading.Event | None = None
        self.started = threading.Event()
        self.fail_next = False

    def __call__(self, plan_id: UUID, outcome: ConfirmationOutcome) -> None:
        self.log.append(f"callback:{outcome}")
        self.threads.append(threading.current_thread())
        self.started.set()
        if self.gate is not None:
            self.gate.wait(WAIT_SECONDS)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("数据库暂时连不上")
        self.calls.append((plan_id, outcome))
