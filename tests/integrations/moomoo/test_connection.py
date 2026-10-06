"""连接 OpenD 的配置和连接的测试。场景见 openspec 的 moomoo-quotes spec「连接配置」。"""

import socket
import threading
from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.integrations.moomoo import MoomooConfig, MoomooQuoteSource, open_quote_context
from tradingagentspro.modules.marketdata import QuoteUnavailableError


def test_default_config() -> None:
    """spec「默认连接配置」。"""
    config = MoomooConfig()

    assert config.host == "127.0.0.1"
    assert config.port == 11111
    assert config.timeout == timedelta(seconds=10)


@pytest.mark.parametrize(
    "changes",
    [
        {"host": ""},
        {"port": 0},
        {"port": 65536},
        {"timeout": timedelta(0)},
        {"timeout": timedelta(seconds=-1)},
    ],
)
def test_invalid_config_is_an_error(changes: dict[str, Any]) -> None:
    """spec「连接配置非法」。"""
    with pytest.raises(ValidationError):
        MoomooConfig(**changes)


def _unused_port() -> int:
    """绑定一个空闲端口再关掉，得到一个没有服务在听的端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _finishes(target: Any) -> bool:
    """在线程里运行 target，5 秒内结束时为真。time 被禁用，用 join 的超时代替计时。"""
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=5)
    return not thread.is_alive()


def test_opend_not_running() -> None:
    """spec「OpenD 没开」：建立连接不等 OpenD；超时 1 秒后取行情报行情不可用。"""
    config = MoomooConfig(port=_unused_port(), timeout=timedelta(seconds=1))
    contexts: list[Any] = []
    errors: list[Exception] = []

    def connect() -> None:
        contexts.append(open_quote_context(config))

    def fetch() -> None:
        try:
            MoomooQuoteSource(contexts[0]).quotes(["AAPL"])
        except Exception as error:
            errors.append(error)

    try:
        assert _finishes(connect)
        assert _finishes(fetch)
        assert len(errors) == 1
        assert isinstance(errors[0], QuoteUnavailableError)
    finally:
        for context in contexts:
            context.close()
