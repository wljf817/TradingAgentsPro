"""SEC EDGAR 公告源：用 SEC 的股票代码表和每家公司的 submissions 实现新闻和公告模块的
FilingSource。

见 sec-filings spec 和 add-news-and-filings 的 design D6、D7。
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from tradingagentspro.contracts.news import Filing
from tradingagentspro.modules.news import SourceUnavailableError

SEC_URL = "https://www.sec.gov"
SEC_DATA_URL = "https://data.sec.gov"
NEW_YORK = ZoneInfo("America/New_York")


class SecConfig(BaseModel):
    """SEC 公告源的配置，见 sec-filings spec「SEC 配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 写明联系方式，由人填写；部署时放在环境变量 SEC_USER_AGENT 里
    user_agent: str = Field(min_length=1)
    # 每次请求等多久没有响应就算超时
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))
    # SEC 的上限是每秒 10 次，超过就封 10 分钟
    max_requests_per_second: int = Field(default=8, ge=1, le=10)


class _Ticker(BaseModel):
    """代码表的一项：只取用到的字段。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    cik_str: int
    ticker: str


_TICKERS = TypeAdapter(dict[str, _Ticker])


class _Recent(BaseModel):
    """submissions 的 filings.recent：按列存放，只取用到的列。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    accession_number: tuple[str, ...] = Field(alias="accessionNumber")
    form: tuple[str, ...]
    acceptance: tuple[str, ...] = Field(alias="acceptanceDateTime")
    items: tuple[str, ...]
    primary_document: tuple[str, ...] = Field(alias="primaryDocument")


class _Filings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    recent: _Recent


class _Submissions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    filings: _Filings


def _wait(duration: timedelta) -> None:
    """阻塞等待这么久；time 模块全项目禁用。"""
    threading.Event().wait(duration.total_seconds())


class SecFilingSource:
    """满足 FilingSource：按股票查 CIK，读每家公司的 submissions，取起始时刻以来受理的公告。

    同一个进程里对 SEC 的请求一个接一个地发，每个请求结束以后等 1 / 每秒最多请求数 秒。调用失败按
    sec-filings spec「公告请求失败的分类」处理：超时、连不上、403、429 和 5xx 抛
    SourceUnavailableError；其余的 4xx 原样抛 httpx.HTTPStatusError；回复读不出来抛 pydantic 的
    ValidationError。任何一个请求失败整次失败，不重试。
    """

    def __init__(
        self,
        config: SecConfig,
        transport: httpx.BaseTransport | None = None,
        pause: Callable[[timedelta], None] = _wait,
    ) -> None:
        """transport、pause 只给测试用：替身 SEC，以及记下等待而不真的等。"""
        self._http = httpx.Client(
            headers={"User-Agent": config.user_agent},
            timeout=config.timeout.total_seconds(),
            transport=transport,
        )
        self._interval = timedelta(seconds=1) / config.max_requests_per_second
        self._pause = pause
        self._lock = threading.Lock()  # 一次调用从头到尾持有：请求依次发出，代码表不被同时改
        self._ciks: dict[str, int] | None = None  # SEC 的股票代码 -> CIK
        self._missing: set[str] = set()  # 重新下载代码表以后仍然查不到的股票

    def filings(self, symbols: Sequence[str], since: datetime) -> tuple[Filing, ...]:
        """这些股票所属公司 since（含）以来受理的公告；同一份公告列在几家公司下时合成一条。"""
        with self._lock:
            found: dict[str, Filing] = {}
            for cik, members in self._companies(symbols).items():
                for filing in self._read(cik, members, since):
                    known = found.get(filing.accession_number)
                    if known is not None:
                        tagged = set(known.symbols) | set(filing.symbols)
                        merged = tuple(s for s in symbols if s in tagged)
                        filing = known.model_copy(update={"symbols": merged})
                    found[filing.accession_number] = filing
            return tuple(found.values())

    def _companies(self, symbols: Sequence[str]) -> dict[int, tuple[str, ...]]:
        """按 CIK 分组的股票，组内按调用方给出的顺序；查不到的股票不在里面。

        内存里没有代码表，或者有不在"查不到"名单里的股票查不到时，下载代码表；下载以后仍然查不到的
        股票记进名单，本进程里不再因为它下载。
        """
        wanted = [s for s in symbols if s not in self._missing]
        ciks = self._ciks
        if ciks is None or any(_sec_ticker(s) not in ciks for s in wanted):
            ciks = self._ciks = self._download_ciks()
        self._missing.update(s for s in wanted if _sec_ticker(s) not in ciks)
        groups: dict[int, list[str]] = {}
        for symbol in symbols:
            if (cik := ciks.get(_sec_ticker(symbol))) is not None:
                groups.setdefault(cik, []).append(symbol)
        return {cik: tuple(members) for cik, members in groups.items()}

    def _download_ciks(self) -> dict[str, int]:
        response = self._get(f"{SEC_URL}/files/company_tickers.json")
        return {e.ticker: e.cik_str for e in _TICKERS.validate_json(response.content).values()}

    def _read(self, cik: int, members: tuple[str, ...], since: datetime) -> Iterator[Filing]:
        """一家公司 since（含）以来受理的公告，标注 members。"""
        response = self._get(f"{SEC_DATA_URL}/submissions/CIK{cik:010d}.json")
        recent = _Submissions.model_validate_json(response.content).filings.recent
        rows = zip(
            recent.accession_number,
            recent.form,
            recent.acceptance,
            recent.items,
            recent.primary_document,
            strict=True,
        )
        for accession, form, acceptance, items, document in rows:
            accepted_at = _accepted_at(acceptance)
            if accepted_at >= since:
                folder = f"{SEC_URL}/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
                yield Filing(
                    accession_number=accession,
                    symbols=members,
                    form=form,
                    items=tuple(items.split(",")) if items else (),
                    accepted_at=accepted_at,
                    url=f"{folder}/{document}",
                )

    def _get(self, url: str) -> httpx.Response:
        """发一个请求，结束以后（成功或失败）等一个间隔。超时、连不上、403、429 和 5xx 抛
        SourceUnavailableError，其余的 4xx 原样抛出。
        """
        try:
            response = self._http.get(url)
        except httpx.TransportError as error:  # 超时也是 TransportError
            raise SourceUnavailableError(f"SEC GET {url}：{error}") from error
        finally:
            self._pause(self._interval)
        if response.status_code in (403, 429) or response.status_code >= 500:
            raise SourceUnavailableError(f"SEC GET {url}：{response.status_code}")
        return response.raise_for_status()


def _sec_ticker(symbol: str) -> str:
    """SEC 代码表的写法：分级股票用横线，BRK.B 是 BRK-B。"""
    return symbol.replace(".", "-")


def _accepted_at(text: str) -> datetime:
    """submissions 的 acceptanceDateTime 比真实时刻晚了美东和 UTC 的时差：换成美东时间，它的钟面
    读数就是真实时刻的 UTC 钟面读数。见 sec-filings spec「受理时刻的换算」和 design D6。
    """
    shown = datetime.fromisoformat(text)  # 比如 2026-07-31T00:30:28.000Z
    return shown.astimezone(NEW_YORK).replace(tzinfo=UTC)  # 2026-07-30T20:30:28Z
