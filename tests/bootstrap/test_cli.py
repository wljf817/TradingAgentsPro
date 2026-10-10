"""命令行入口的测试。

场景见 openspec 的 bootstrap spec「命令行」「停止」，做法见 add-bootstrap-wiring 的 design D2、D10。
"""

import logging
import os
import signal
import sys
import threading
from collections.abc import Iterator, Mapping
from importlib.metadata import version
from typing import Any

import pytest

from tradingagentspro.bootstrap import cli


class Recorded:
    """替代 app.run：记下收到的参数，返回给定的退出码。"""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[tuple[Mapping[str, str], threading.Event]] = []

    def __call__(self, environ: Mapping[str, str], stop: threading.Event) -> int:
        self.calls.append((environ, stop))
        return self.code


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorded]:
    """把 app.run 换成 Recorded；测试结束时恢复信号处理和日志设置。"""
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    touched = [logging.getLogger(name) for name in ("", "dbos", "httpx", "httpcore")]
    saved: list[tuple[Any, int, bool]] = [
        (lg.handlers[:], lg.level, lg.propagate) for lg in touched
    ]
    fake = Recorded()
    monkeypatch.setattr(cli, "run", fake)
    yield fake
    for sig, handler in handlers.items():
        signal.signal(sig, handler)
    for lg, (old, level, propagate) in zip(touched, saved, strict=True):
        lg.handlers[:] = old
        lg.setLevel(level)
        lg.propagate = propagate


def test_version(capsys: pytest.CaptureFixture[str], recorded: Recorded) -> None:
    """spec「打印版本号」：退出码 0，没有启动程序。"""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"tradingagentspro {version('tradingagentspro')}\n"
    assert recorded.calls == []


@pytest.mark.parametrize("argv", [[], ["start"]])
def test_usage_error(
    argv: list[str], capsys: pytest.CaptureFixture[str], recorded: Recorded
) -> None:
    """spec「用法不对」：没有子命令或者子命令不认识，打印用法，退出码 2。"""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)

    assert exit_info.value.code == 2
    assert "usage: tradingagentspro" in capsys.readouterr().err
    assert recorded.calls == []


def test_run_hands_over_the_environment(recorded: Recorded) -> None:
    """`run` 把进程的环境变量和停止事件交给 app.run，返回它的退出码。"""
    recorded.code = 1

    code = cli.main(["run"])

    assert code == 1
    [(environ, stop)] = recorded.calls
    assert environ is os.environ
    assert not stop.is_set()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signals_set_the_stop_event(recorded: Recorded, sig: signal.Signals) -> None:
    """spec「SIGTERM」「SIGINT」：两个信号都只设置停止事件，不抛 KeyboardInterrupt。"""
    cli.main(["run"])
    [(_, stop)] = recorded.calls

    os.kill(os.getpid(), sig)

    assert stop.wait(5)


def test_run_configures_logging(recorded: Recorded) -> None:
    """`run` 先配置日志：根 logger 是 INFO，httpx 是 WARNING。"""
    cli.main(["run"])

    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING


class _Exits:
    """替代 os._exit 和 logging.shutdown：记下调用，不真的结束进程、不关日志。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.codes: list[int] = []
        self.shutdowns = 0
        monkeypatch.setattr(os, "_exit", self.codes.append)
        monkeypatch.setattr(logging, "shutdown", self._shutdown)

    def _shutdown(self) -> None:
        self.shutdowns += 1


@pytest.mark.parametrize("code", [0, 1])
def test_entry_ends_the_process_after_main(
    monkeypatch: pytest.MonkeyPatch, recorded: Recorded, code: int
) -> None:
    """spec「停止时有方案在等确认」：main 返回以后关掉日志、立即结束进程，退出码是 main 的返回值。

    见 add-order-flow 的 design D11。
    """
    exits = _Exits(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["tradingagentspro", "run"])
    recorded.code = code

    cli.entry()

    assert exits.codes == [code]
    assert exits.shutdowns == 1


def test_entry_lets_argparse_exit(monkeypatch: pytest.MonkeyPatch, recorded: Recorded) -> None:
    """--version 时 argparse 照常退出，不调用 os._exit。"""
    exits = _Exits(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["tradingagentspro", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        cli.entry()

    assert exit_info.value.code == 0
    assert exits.codes == []
