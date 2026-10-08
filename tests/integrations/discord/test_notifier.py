"""DiscordNotifier 的测试：启动和关闭、同步调用、重复请求、结束确认和按钮的处理。

用替身 Gateway 和替身按钮，不连 Discord。每个测试对应 openspec 的 discord-notification spec
里的一个场景，线程模型见 add-discord-notify 的 design D7。
"""

import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from functools import partial

import discord
import pytest
from pydantic import SecretStr

from tests.fakes.clock import FakeClock
from tests.integrations.discord.gateway import (
    WAIT_SECONDS,
    Decisions,
    FakeGateway,
    FakePress,
)
from tests.modules.notification.builders import (
    CHANNEL_ID,
    DEADLINE,
    GUILD_ID,
    OTHER_USER_ID,
    P1_ID,
    REQUESTED_AT,
    USER_ID,
    p1_request,
)
from tradingagentspro.integrations.discord import DiscordConfig, DiscordNotifier
from tradingagentspro.integrations.discord.messages import button_id
from tradingagentspro.integrations.discord.notifier import ButtonPress, Gateway
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    NotificationUnavailableError,
    Notifier,
    PendingConfirmation,
)

FAST = timedelta(milliseconds=200)  # 测试里的发送超时和连接超时，让超时的场景跑得快


def _config(**changes: timedelta) -> DiscordConfig:
    fields = {"connect_timeout": FAST, "send_timeout": FAST, **changes}
    return DiscordConfig(
        token=SecretStr("test-token"),
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        user_id=USER_ID,
        **fields,
    )


class Setup:
    """一个 DiscordNotifier 和它的替身：Gateway、时钟、回调。"""

    def __init__(self, config: DiscordConfig) -> None:
        self.gateway = FakeGateway()
        self.clock = FakeClock(REQUESTED_AT)
        self.log: list[str] = []
        self.decisions = Decisions(self.log)
        checked: Gateway = self.gateway  # 让 mypy 检查替身满足协议
        self.notifier = DiscordNotifier(checked, config, self.clock, self.decisions)

    def start(self) -> "Setup":
        self.notifier.start()
        return self


@pytest.fixture
def make() -> Iterator[Callable[..., Setup]]:
    """建 DiscordNotifier；测试结束时全部关闭，并检查 discord 线程都已经结束。"""
    created: list[Setup] = []

    def factory(**changes: timedelta) -> Setup:
        setup = Setup(_config(**changes))
        created.append(setup)
        return setup

    yield factory
    for setup in created:
        setup.notifier.close()
    alive = [t for t in threading.enumerate() if t.name == "discord" and t.is_alive()]
    assert alive == []


def _in_thread(action: Callable[[], object]) -> threading.Thread:
    """在另一个线程里调用 action，返回已经开始的线程。"""
    thread = threading.Thread(target=action)
    thread.start()
    return thread


def _finishes_quickly(action: Callable[[], object]) -> BaseException | None:
    """在另一个线程里调用 action，1 秒内必须结束；返回它抛的异常。"""
    errors: list[BaseException] = []

    def run() -> None:
        try:
            action()
        except BaseException as error:
            errors.append(error)

    thread = _in_thread(run)
    thread.join(1)
    assert not thread.is_alive()
    return errors[0] if errors else None


def test_satisfies_the_port(make: Callable[..., Setup]) -> None:
    """DiscordNotifier 满足通知模块的 Notifier。"""
    notifier: Notifier = make().notifier
    assert notifier is not None


# 启动和关闭


def test_start_then_alert(make: Callable[..., Setup]) -> None:
    """spec「正常启动」，以及「推送告警」：经过 Gateway 发出，开头 @ 本人，只允许通知本人。"""
    setup = make().start()

    setup.notifier.alert("AAPL 没有止损单：挂单失败")

    [sent] = setup.gateway.sent
    assert sent.content == "<@1001> AAPL 没有止损单：挂单失败"
    assert sent.view is None
    assert sent.mentions.everyone is False
    assert isinstance(sent.mentions.users, list)
    assert [user.id for user in sent.mentions.users] == [USER_ID]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NotificationUnavailableError("Discord 登录失败"), "登录失败"),
        (NotificationUnavailableError(f"频道 {CHANNEL_ID} 不在服务器 {GUILD_ID} 里"), "2002"),
    ],
    ids=["token 无效", "频道不对"],
)
def test_start_fails(make: Callable[..., Setup], error: Exception, expected: str) -> None:
    """spec「token 无效」「频道不对」：Gateway 启动失败时，启动报通知不可用并写明原因。"""
    setup = make()
    setup.gateway.run_error = error

    with pytest.raises(NotificationUnavailableError, match=expected):
        setup.notifier.start()


def test_start_times_out(make: Callable[..., Setup]) -> None:
    """spec「连不上」：超过连接超时还没连上，启动失败。"""
    setup = make()
    setup.gateway.never_ready = True

    with pytest.raises(NotificationUnavailableError, match="没有连上"):
        setup.notifier.start()


def test_calls_before_start_fail_at_once(make: Callable[..., Setup]) -> None:
    """spec「启动之前或关闭以后调用」：还没启动时，不等发送超时。"""
    setup = make(send_timeout=timedelta(seconds=10))
    notifier = setup.notifier

    for call in (
        lambda: notifier.alert("告警"),
        lambda: notifier.request_confirmation(p1_request()),
        lambda: notifier.finish_confirmation("101", ConfirmationOutcome.TIMED_OUT),
    ):
        error = _finishes_quickly(call)
        assert isinstance(error, NotificationUnavailableError)
        assert "还没启动" in str(error)


def test_calls_after_close_fail_at_once(make: Callable[..., Setup]) -> None:
    """spec「启动之前或关闭以后调用」：已经关闭时，不等发送超时。"""
    setup = make(send_timeout=timedelta(seconds=10)).start()
    setup.notifier.close()

    error = _finishes_quickly(lambda: setup.notifier.alert("告警"))

    assert isinstance(error, NotificationUnavailableError)
    assert setup.gateway.sent == []


# 重复请求同一个方案


def test_retry_after_timeout_returns_the_late_message(make: Callable[..., Setup]) -> None:
    """spec「超时以后重试」。"""
    setup = make().start()
    setup.gateway.hold_sends()

    with pytest.raises(NotificationUnavailableError, match="没有完成"):
        setup.notifier.request_confirmation(p1_request())
    setup.gateway.release_sends()
    pending = setup.notifier.request_confirmation(p1_request(DEADLINE + timedelta(seconds=15)))

    assert len(setup.gateway.sent) == 1
    assert pending == PendingConfirmation(message_id="101", deadline=DEADLINE)


def test_retry_after_failure_sends_again(make: Callable[..., Setup]) -> None:
    """spec「第一次发送失败」。"""
    setup = make().start()
    setup.gateway.fail_next_send(NotificationUnavailableError("Discord：503 Service Unavailable"))

    with pytest.raises(NotificationUnavailableError):
        setup.notifier.request_confirmation(p1_request())
    pending = setup.notifier.request_confirmation(p1_request())

    assert len(setup.gateway.sent) == 1
    assert pending.message_id == "101"


def test_confirmation_message_has_content_and_buttons(make: Callable[..., Setup]) -> None:
    """spec「默认 30 分钟的截止时刻」「买入方案通过风控」：发出的确认消息带正文和两个按钮。"""
    setup = make().start()

    pending = setup.notifier.request_confirmation(p1_request())

    [sent] = setup.gateway.sent
    assert sent.content.startswith("<@1001> 请确认：买入 AAPL 100 股\n")
    assert sent.view is not None
    assert len(sent.view.children) == 2
    assert pending == PendingConfirmation(message_id="101", deadline=DEADLINE)


# 结束确认


@pytest.mark.parametrize(
    ("outcome", "title"),
    [(ConfirmationOutcome.TIMED_OUT, "已超时"), (ConfirmationOutcome.VOIDED, "已作废")],
    ids=["报告超时", "报告作废"],
)
def test_finish(make: Callable[..., Setup], outcome: ConfirmationOutcome, title: str) -> None:
    """spec「报告超时」「报告作废」：消息去掉按钮，显示报告的结果。"""
    setup = make().start()
    pending = setup.notifier.request_confirmation(p1_request())

    setup.notifier.finish_confirmation(pending.message_id, outcome)

    assert setup.gateway.edits == [(101, title)]


def test_finish_on_deleted_message(make: Callable[..., Setup]) -> None:
    """spec「消息已被删掉」：什么也不做，不报错。"""
    setup = make().start()
    pending = setup.notifier.request_confirmation(p1_request())
    setup.gateway.deleted.add(101)

    setup.notifier.finish_confirmation(pending.message_id, ConfirmationOutcome.TIMED_OUT)

    assert setup.gateway.edits == []


# 同步调用和失败


def test_calls_from_two_threads(make: Callable[..., Setup]) -> None:
    """spec「从多个线程同时调用」。"""
    setup = make(send_timeout=timedelta(seconds=WAIT_SECONDS)).start()
    errors: list[BaseException] = []

    def alert(text: str) -> None:
        try:
            setup.notifier.alert(text)
        except BaseException as error:
            errors.append(error)

    threads = [_in_thread(partial(alert, text)) for text in ("第一条", "第二条")]
    for thread in threads:
        thread.join(WAIT_SECONDS)

    assert errors == []
    assert sorted(sent.content for sent in setup.gateway.sent) == [
        "<@1001> 第一条",
        "<@1001> 第二条",
    ]


def test_send_timeout(make: Callable[..., Setup]) -> None:
    """spec「超过发送超时」。"""
    setup = make().start()
    setup.gateway.hold_sends()

    with pytest.raises(NotificationUnavailableError, match="没有完成"):
        setup.notifier.request_confirmation(p1_request())
    setup.gateway.release_sends()


def test_discord_error(make: Callable[..., Setup]) -> None:
    """spec「Discord 报错」：错误信息里有 Discord 给的原因。"""
    setup = make().start()
    setup.gateway.fail_next_send(NotificationUnavailableError("Discord：403 Missing Permissions"))

    with pytest.raises(NotificationUnavailableError, match="Missing Permissions"):
        setup.notifier.request_confirmation(p1_request())


def test_recovers_after_network_outage(make: Callable[..., Setup]) -> None:
    """spec「网络中断以后恢复」。"""
    setup = make().start()
    setup.gateway.fail_next_send(NotificationUnavailableError("Discord：网络不通"))

    with pytest.raises(NotificationUnavailableError):
        setup.notifier.alert("第一条")
    setup.notifier.alert("第二条")

    assert [sent.content for sent in setup.gateway.sent] == ["<@1001> 第二条"]


def test_connection_aborted(make: Callable[..., Setup], caplog: pytest.LogCaptureFixture) -> None:
    """spec「连接意外中止」：立即报通知不可用，并写一条错误日志。"""
    setup = make(send_timeout=timedelta(seconds=10)).start()

    with caplog.at_level(logging.ERROR):
        setup.gateway.abort(NotificationUnavailableError("token 已失效"))
        assert setup.gateway.loop_thread is not None
        setup.gateway.loop_thread.join(WAIT_SECONDS)
        error = _finishes_quickly(lambda: setup.notifier.alert("告警"))

    assert isinstance(error, NotificationUnavailableError)
    assert "中止" in str(error)
    assert any("中止" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


# 按钮的处理


def _press(
    setup: Setup,
    user_id: int = USER_ID,
    outcome: ConfirmationOutcome = ConfirmationOutcome.CONFIRMED,
    message_id: int = 101,
) -> FakePress:
    """对 P1 的确认消息（截止 14:30Z）按一次按钮，按下的记录和回调共用一个 log。"""
    custom_id = button_id(P1_ID, outcome, DEADLINE)
    press = FakePress(user_id, custom_id, message_id, setup.log)
    checked: ButtonPress = press  # 让 mypy 检查替身满足协议
    setup.gateway.press(checked)
    return press


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 8, hour, minute, second, tzinfo=UTC)


def test_user_confirms(make: Callable[..., Setup]) -> None:
    """spec「本人按确认」：先应答，再调用回调，回调成功以后去掉按钮、显示已确认和时刻。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))

    press = _press(setup)

    assert setup.decisions.calls == [(P1_ID, ConfirmationOutcome.CONFIRMED)]
    assert setup.log == ["defer", "callback:confirmed", "show:已确认"]
    assert press.shown[0].timestamp == _at(14, 5)


def test_user_rejects(make: Callable[..., Setup]) -> None:
    """spec「本人按拒绝」。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))

    _press(setup, outcome=ConfirmationOutcome.REJECTED)

    assert setup.decisions.calls == [(P1_ID, ConfirmationOutcome.REJECTED)]
    assert setup.log == ["defer", "callback:rejected", "show:已拒绝"]


def test_other_user_is_refused(make: Callable[..., Setup]) -> None:
    """spec「别人按按钮」：只回复他自己，回调没有被调用，消息不变。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))

    _press(setup, user_id=OTHER_USER_ID)

    assert setup.log == ["reply:只有 <@1001> 可以确认或拒绝这个方案"]
    assert setup.decisions.calls == []
    assert setup.gateway.edits == []


@pytest.mark.parametrize("second", [ConfirmationOutcome.CONFIRMED, ConfirmationOutcome.REJECTED])
def test_second_press_while_busy(make: Callable[..., Setup], second: ConfirmationOutcome) -> None:
    """spec「连按两次」：回调还没返回时再按，只得到「还在处理」，回调只调用一次。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))
    gate = setup.decisions.gate = threading.Event()
    first = _in_thread(lambda: _press(setup))
    assert setup.decisions.started.wait(WAIT_SECONDS)

    again = FakePress(USER_ID, button_id(P1_ID, second, DEADLINE), 101)
    setup.gateway.press(again)
    gate.set()
    first.join(WAIT_SECONDS)

    assert again.log == ["reply:上一次操作还在处理，请稍等"]
    assert setup.decisions.calls == [(P1_ID, ConfirmationOutcome.CONFIRMED)]


def test_press_after_outcome(make: Callable[..., Setup]) -> None:
    """spec「已经有结果以后再按」。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))
    _press(setup)

    again = FakePress(USER_ID, button_id(P1_ID, ConfirmationOutcome.CONFIRMED, DEADLINE), 101)
    setup.gateway.press(again)

    assert again.log == ["reply:这个方案已确认"]
    assert len(setup.decisions.calls) == 1


def test_press_one_second_before_deadline(make: Callable[..., Setup]) -> None:
    """spec「截止前一秒按下」。"""
    setup = make().start()
    setup.clock.set(_at(14, 29, 59))

    _press(setup)

    assert setup.decisions.calls == [(P1_ID, ConfirmationOutcome.CONFIRMED)]
    assert setup.log[-1] == "show:已确认"


@pytest.mark.parametrize(
    ("now", "outcome"),
    [
        (_at(14, 30), ConfirmationOutcome.CONFIRMED),
        (_at(14, 45), ConfirmationOutcome.REJECTED),
    ],
    ids=["正好截止", "截止以后"],
)
def test_press_at_or_after_deadline(
    make: Callable[..., Setup], now: datetime, outcome: ConfirmationOutcome
) -> None:
    """spec「截止时刻或之后按下」：不调用回调，消息去掉按钮、显示已超时。"""
    setup = make().start()
    setup.clock.set(now)

    _press(setup, outcome=outcome)

    assert setup.decisions.calls == []
    assert setup.log == ["show:已超时"]


def test_acknowledges_before_callback_and_keeps_working(make: Callable[..., Setup]) -> None:
    """spec「先应答再调用回调」：回调不在事件循环的线程里运行，运行期间告警和别的按钮照常处理。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))
    gate = setup.decisions.gate = threading.Event()
    first = _in_thread(lambda: _press(setup))
    assert setup.decisions.started.wait(WAIT_SECONDS)

    assert setup.log == ["defer", "callback:confirmed"]
    setup.notifier.alert("回调运行期间的告警")
    other = FakePress(OTHER_USER_ID, button_id(P1_ID, ConfirmationOutcome.REJECTED, DEADLINE), 101)
    setup.gateway.press(other)
    gate.set()
    first.join(WAIT_SECONDS)

    assert [sent.content for sent in setup.gateway.sent] == ["<@1001> 回调运行期间的告警"]
    assert other.log == ["reply:只有 <@1001> 可以确认或拒绝这个方案"]
    assert setup.decisions.threads[0] is not setup.gateway.loop_thread
    assert setup.log == ["defer", "callback:confirmed", "show:已确认"]


def test_callback_failure_keeps_buttons(
    make: Callable[..., Setup], caplog: pytest.LogCaptureFixture
) -> None:
    """spec「回调抛错」：请人再按一次，按钮还在；回调恢复以后再按就送达。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))
    setup.decisions.fail_next = True

    with caplog.at_level(logging.ERROR):
        _press(setup)

    assert setup.log == ["defer", "callback:confirmed", "reply:没有送达，请再按一次"]
    assert setup.decisions.calls == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)

    setup.log.clear()
    _press(setup)

    assert setup.decisions.calls == [(P1_ID, ConfirmationOutcome.CONFIRMED)]
    assert setup.log == ["defer", "callback:confirmed", "show:已确认"]


def test_press_on_message_sent_before_restart(make: Callable[..., Setup]) -> None:
    """spec「重启以后按旧消息的按钮」：新的进程不记得这条消息，按钮照样能用。"""
    before = make().start()
    before.notifier.request_confirmation(p1_request())
    [sent] = before.gateway.sent
    assert sent.view is not None
    custom_id = next(
        item.custom_id
        for item in sent.view.children
        if isinstance(item, discord.ui.Button) and item.label == "确认"
    )
    assert custom_id is not None
    before.notifier.close()

    after = make().start()
    after.clock.set(_at(14, 5))
    press = FakePress(USER_ID, custom_id, sent.message_id, after.log)
    after.gateway.press(press)

    assert after.decisions.calls == [(P1_ID, ConfirmationOutcome.CONFIRMED)]
    assert after.log == ["defer", "callback:confirmed", "show:已确认"]


@pytest.mark.parametrize("custom_id", ["plan:new", "confirmation:maybe"])
def test_other_interactions_are_ignored(make: Callable[..., Setup], custom_id: str) -> None:
    """spec「别的按钮或命令」：不应答、不调用回调。"""
    setup = make().start()
    press = FakePress(USER_ID, custom_id, 101, setup.log)

    setup.gateway.press(press)

    assert setup.log == []
    assert setup.decisions.calls == []


def test_every_press_is_logged(
    make: Callable[..., Setup], caplog: pytest.LogCaptureFixture
) -> None:
    """spec「按钮的处理」：每次按下写一条日志，有谁、哪个方案、按了什么、怎样处理的。"""
    setup = make().start()
    setup.clock.set(_at(14, 5))

    with caplog.at_level(logging.INFO, logger="tradingagentspro.integrations.discord"):
        _press(setup, user_id=OTHER_USER_ID)
        _press(setup)

    messages = [r.getMessage() for r in caplog.records if "按钮" in r.getMessage()]
    assert len(messages) == 2
    assert str(OTHER_USER_ID) in messages[0]
    assert str(P1_ID) in messages[0]
    assert "确认" in messages[1]
    assert str(USER_ID) in messages[1]


# 结束确认和按钮的先后


def test_caller_outcome_overrides_button(make: Callable[..., Setup]) -> None:
    """spec「按钮的结果和调用方的结果不一致」：调用方报告的结果覆盖已确认。"""
    setup = make().start()
    setup.clock.set(_at(14, 9))
    _press(setup)

    setup.notifier.finish_confirmation("101", ConfirmationOutcome.VOIDED)

    assert setup.log[-1] == "show:已确认"
    assert setup.gateway.edits == [(101, "已作废")]


def test_finish_while_callback_runs(make: Callable[..., Setup]) -> None:
    """spec「报告时回调还在运行」：回调返回以后不再改消息。"""
    setup = make().start()
    setup.clock.set(_at(14, 9))
    gate = setup.decisions.gate = threading.Event()
    first = _in_thread(lambda: _press(setup))
    assert setup.decisions.started.wait(WAIT_SECONDS)

    setup.notifier.finish_confirmation("101", ConfirmationOutcome.VOIDED)
    gate.set()
    first.join(WAIT_SECONDS)

    assert setup.gateway.edits == [(101, "已作废")]
    assert setup.log == ["defer", "callback:confirmed"]


def test_press_after_finish(make: Callable[..., Setup]) -> None:
    """spec「报告以后再按」。"""
    setup = make().start()
    setup.clock.set(_at(14, 10))
    setup.notifier.finish_confirmation("101", ConfirmationOutcome.VOIDED)

    press = _press(setup)

    assert press.log == ["reply:这个方案已作废"]
    assert setup.decisions.calls == []
