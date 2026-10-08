"""日志配置的测试。场景见 openspec 的 bootstrap spec「日志」。"""

import io
import logging
from datetime import UTC, datetime

import pytest
from dbos import DBOS


def _lines(log_stream: io.StringIO) -> list[str]:
    return log_stream.getvalue().splitlines()


def test_time_is_utc(log_stream: io.StringIO) -> None:
    """spec「时间用 UTC」：进程在 UTC+14，行首仍然是 UTC 时间，然后是级别、线程名、logger 名。"""
    created = datetime(2026, 10, 14, 14, 0, 0, 123000, tzinfo=UTC).timestamp()
    record = logging.makeLogRecord(
        {
            "name": "tradingagentspro.test",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "你好 %s",
            "args": ("世界",),
            "created": created,
            "threadName": "discord",
        }
    )

    logging.getLogger("tradingagentspro.test").handle(record)

    assert _lines(log_stream) == [
        "2026-10-14T14:00:00.123Z INFO [discord] tradingagentspro.test: 你好 世界"
    ]


@pytest.mark.parametrize("library", ["httpx", "httpcore"])
def test_http_request_logs_are_hidden(log_stream: io.StringIO, library: str) -> None:
    """spec「httpx 的请求日志不输出」：INFO 不输出，WARNING 照常输出。"""
    logger = logging.getLogger(f"{library}.client")

    logger.info("HTTP Request: GET https://paper-api.alpaca.markets/v2/account")
    logger.warning("连接池满了")

    lines = _lines(log_stream)
    assert len(lines) == 1
    assert "WARNING" in lines[0]
    assert "连接池满了" in lines[0]


def test_debug_is_hidden(log_stream: io.StringIO) -> None:
    """spec「DEBUG 不输出」。"""
    logger = logging.getLogger("tradingagentspro.bootstrap.test")

    logger.debug("看不到")
    logger.info("看得到")

    lines = _lines(log_stream)
    assert len(lines) == 1
    assert lines[0].endswith("INFO [MainThread] tradingagentspro.bootstrap.test: 看得到")


def test_dbos_logs_go_through_the_same_output_once(log_stream: io.StringIO) -> None:
    """spec「DBOS 的日志」：dbos logger 的 INFO 经同一个输出、同一种格式，只出现一次；
    之后构造 DBOS 也不会加它自己的控制台 handler。
    """
    DBOS(config={"name": "logs-test", "system_database_url": "postgresql://u@127.0.0.1:1/x"})
    try:
        dbos_logger = logging.getLogger("dbos")
        assert dbos_logger.propagate
        assert all(isinstance(h, logging.NullHandler) for h in dbos_logger.handlers)
        dbos_logger.info("DBOS 的一条日志")
    finally:
        DBOS.destroy()

    matching = [line for line in _lines(log_stream) if "DBOS 的一条日志" in line]
    assert len(matching) == 1
    assert " INFO [MainThread] dbos: DBOS 的一条日志" in matching[0]


def test_exceptions_carry_the_stack(log_stream: io.StringIO) -> None:
    """spec「日志」：异常带上异常栈。"""
    try:
        raise RuntimeError("出错了")
    except RuntimeError:
        logging.getLogger("tradingagentspro.test").exception("处理失败")

    text = log_stream.getvalue()
    assert "ERROR [MainThread] tradingagentspro.test: 处理失败" in text
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: 出错了" in text
