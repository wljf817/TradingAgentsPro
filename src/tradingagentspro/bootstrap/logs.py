"""日志配置：全部日志写到 stderr，时间用 UTC，级别 INFO。

见 bootstrap spec「日志」和 add-bootstrap-wiring 的 design D4。这里只写启动和停止的过程和技术问题，
由 systemd 交给 journald；程序的动作写进数据库里的程序日志，见 program-log spec。
"""

import logging
from datetime import UTC, datetime
from typing import TextIO

FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"

# 每个请求写一条 INFO 的库：只输出 WARNING 及以上
QUIET = ("httpx", "httpcore")


class UtcFormatter(logging.Formatter):
    """时间按 UTC 写成 2026-10-14T14:00:00.123Z，不依赖本机时区。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created, UTC)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def configure_logging(stream: TextIO | None = None) -> None:
    """根 logger 只留一个写到 stream（默认 stderr）的 handler，级别 INFO。

    要在构造 DBOS 之前调用：dbos logger 换成一个 NullHandler 并向上传递，DBOS 看到已有 handler，
    就不再加它自己的控制台 handler，日志经根 logger 用同一种格式输出，每条一次。
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(UtcFormatter(FORMAT))
    root = logging.getLogger()
    for old in root.handlers[:]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    dbos = logging.getLogger("dbos")
    for old in dbos.handlers[:]:
        dbos.removeHandler(old)
    dbos.addHandler(logging.NullHandler())
    dbos.propagate = True
