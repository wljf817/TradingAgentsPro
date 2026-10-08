"""命令行入口：tradingagentspro run 启动交易侧程序，tradingagentspro --version 打印版本号。

见 bootstrap spec「命令行」「停止」和 add-bootstrap-wiring 的 design D2、D10。
"""

import argparse
import os
import signal
import threading
from collections.abc import Sequence
from importlib.metadata import version
from types import FrameType

from tradingagentspro.bootstrap.app import run
from tradingagentspro.bootstrap.logs import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并运行，返回退出码；用法不对时 argparse 以退出码 2 退出。"""
    parser = argparse.ArgumentParser(prog="tradingagentspro", description="AI Agent 美股交易系统")
    parser.add_argument(
        "--version", action="version", version=f"tradingagentspro {version('tradingagentspro')}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="启动交易侧程序（模拟盘），收到 SIGTERM 或 SIGINT 后停止")
    parser.parse_args(argv)

    configure_logging()
    stop = threading.Event()
    stop_on_signals(stop)
    return run(os.environ, stop)


def stop_on_signals(stop: threading.Event) -> None:
    """SIGTERM 和 SIGINT 都只设置 stop：处理函数里不写日志，由 run 在醒来以后写。"""

    def handle(signum: int, frame: FrameType | None) -> None:
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle)
