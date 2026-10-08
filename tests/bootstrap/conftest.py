"""bootstrap 测试共用的夹具：把日志配置到一个 StringIO，测试结束后恢复原来的日志设置。"""

import io
import logging
from collections.abc import Iterator

import pytest

from tradingagentspro.bootstrap.logs import configure_logging

# 这些 logger 的设置会被 configure_logging 改掉，每个测试前后保存和恢复
_TOUCHED = ("", "dbos", "httpx", "httpcore")


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """配置好日志、写到一个 StringIO 的输出；测试结束后恢复原来的日志设置。"""
    loggers = [logging.getLogger(name) for name in _TOUCHED]
    saved = [(lg.handlers[:], lg.level, lg.propagate) for lg in loggers]
    output = io.StringIO()
    configure_logging(output)
    yield output
    for lg, (handlers, level, propagate) in zip(loggers, saved, strict=True):
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
