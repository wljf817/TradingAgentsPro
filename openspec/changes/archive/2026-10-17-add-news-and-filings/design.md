# Design

## Context

现状：

- `modules/marketdata` 是行情模块：端口 `QuoteSource`、失败时抛 `QuoteUnavailableError`、不重试，integrations/moomoo 实现它。本变更照这个写法，但不改它
- `integrations/alpaca/client.py` 的 `AlpacaClient` 连 Trading API，失败分类抛执行模块的 `BrokerUnavailableError`、`OrderRejectedError`；`AlpacaConfig.base_url` 是 Trading API 的地址
- 状态库的两种表：`VersionedStore` 的版本表（按键保存版本，内容变了写新版本）；一行一条的只追加表（程序日志、Agent 网关的三张表，后者用 `INSERT … ON CONFLICT DO NOTHING` 保证一个 Task 只有一个 Result，见 update-task-cost-storage 的 design D1、D3）。迁移到 v0007
- `contracts/event.py` 已有事件类别（新闻、公告、财报发布），事件的完整格式由「事件检测」定
- time 模块全项目禁用；等待用 `threading.Event().wait`（巡检就是这样等的）
- `pyproject.toml` 的冒烟测试标记有 `alpaca_paper`、`moomoo_opend`、`discord_bot`，`addopts` 默认排除

数据源（2026-10-17 实际调用过 SEC；Alpaca 新闻接口人已经实测过免费档）：

- Alpaca 新闻：`GET https://data.alpaca.markets/v1beta1/news`，参数 symbols、start、end、limit（最多 50）、sort、include_content、page_token；回复 `{"news": [...], "next_page_token": ...}`。每条有 id（整数）、headline、author、created_at、updated_at、summary、content、url、images、symbols、source。Alpaca 的 Python SDK 把 url 标为可以为 null（"if applicable"），其余都是必有的字符串
- 2026-10-17 用免费档的 key 取了 1000 条真实回复核对：每条都有全部字段；url 都是非空的地址，没有 null；author 都不为空；41% 的条目摘要、正文和图片都为空（只有标题的快讯）；created_at 是 `2026-09-01T01:21:19Z` 这样到秒的 UTC；没有发布时刻早于 start 的条目，start 按发布时刻筛选
- SEC 代码表 `https://www.sec.gov/files/company_tickers.json`：约 1 万项，800 KB；`{"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}, …}`。分级股票用横线（BRK-B、BF-B）；GOOG 和 GOOGL 是同一个 CIK
- SEC submissions `https://data.sec.gov/submissions/CIK0000320193.json`：AAPL 的 163 KB；`filings.recent` 是按列存放的数组（accessionNumber、filingDate、acceptanceDateTime、form、items、primaryDocument 等），最近 1000 份或至少一年，更早的在 `filings.files` 列出的分页里。别人以这家公司为对象提交的公告（13G、13D）也列在这家公司下，表单类型 2024-12 起是 `SCHEDULE 13G`、`SCHEDULE 13D`，以前是 `SC 13G`、`SC 13D`
- 受理时刻有偏差：AAPL 的 8-K 0000320193-26-000018 在公告页面上写"Accepted 2026-07-30 16:30:28"（美东），submissions 里是 `2026-07-31T00:30:28.000Z`；冬令时的 0000320193-26-000005 页面写 16:30:33，submissions 是 `2026-01-30T02:30:33.000Z`。10-Q、Form 4 同样如此。也就是 submissions 的值 = 真实 UTC 时刻 + 美东和 UTC 的时差
- SEC 对没有写明联系方式的 User-Agent 返回 403；超过每秒 10 次同样返回 403，并封 10 分钟。SEC 查不到的 CIK 返回 404
- 主文件按 `https://www.sec.gov/Archives/edgar/data/320193/000114036126039785/xslF345X06/form4.xml` 这样的地址能直接打开，代理人提交的 Form 4 也可以用公司的 CIK

动机见 proposal.md，要求见 specs/。

### 模块边界

```
contracts/news.py                新：NewsArticle、Filing
modules/news/                    新（新闻和公告）
  __init__.py   导出 NewsService、NewsSource、FilingSource、NewsStore、SourceUnavailableError、
                PostgresNewsStore
  ports.py      NewsSource、FilingSource、NewsStore、SourceUnavailableError
  service.py    NewsService
  repo.py       表 news_articles、news_filings；PostgresNewsStore
integrations/alpaca/news.py      新：AlpacaNewsConfig、AlpacaNewsSource；alpaca/__init__.py 导出
integrations/sec/                新：__init__.py、edgar.py（SecConfig、SecFilingSource）
bootstrap/migrations/versions/v0008_create_news_tables.py
```

- 跨模块传递的 Pydantic 模型：`NewsArticle`、`Filing`（contracts）。两个适配器生成它们，模块存下并返回它们，之后「事件检测」「状态库快照」、Agent 网关读它们
- 新闻和公告模块只依赖 contracts 和 shared；两个适配器只依赖模块的包根和 contracts
- 模块没有 domain 和 config：去重和排序各一行，没有阈值；配置都在适配器
- 不改别的模块、workflows、bootstrap 的装配、shared；纯区域白名单不变（contracts 只用 datetime 和 pydantic）

## Goals / Non-Goals

**Goals:**

- 调用方给出股票和起始时刻，拿到这次新存下的新闻和公告，重复调用、同时调用都不会重复返回同一条
- 两个数据源的失败只分两类：数据源不可用（下一轮再取）和原样报错（配置或程序的问题）
- 受理时刻和发布时刻都是真实的 UTC 时刻
- 不超过 SEC 的频率限制

**Non-Goals:**

- 读取接口只有"一只股票某个时刻以来的"，别的读法由「状态库快照」需要时再加
- 表之间没有外键，没有禁止修改和删除的触发器：和别的只追加表一样，程序只有追加和读取

按「编程风格」省略的情况：

- 一次请求的股票太多、URL 超长：股票池加持仓不超过一百多只，几百个字符
- Alpaca 新闻的 end 参数不传：默认到现在
- 同一家公司的两只股票先后进池：先存下的公告只标注了当时问到的那只
- 同一份公告列在两家公司下、两家公司在不同的调用里取：只标注先存下那次的股票
- submissions 的 `filings.files` 分页不读：recent 至少覆盖一年，起始时刻不会早于这个
- 代码表下载以后，某个代码被换给了另一家公司：进程重启前仍按旧的 CIK 取
- 记为查不到的股票后来被 SEC 收录：进程重启前仍然没有公告
- 夏令时切换那一小时受理的公告：切换在周日凌晨，EDGAR 不受理
- submissions 的几列数组长度不一样：按列配对时报错，原样抛出
- 代码表给出的 CIK 读 submissions 返回 404：原样报错
- 新闻源给出的新闻没有标注任何股票：照样存，读一只股票时读不到
- 新闻 ID 在不同新闻源之间冲突：只有一个新闻源

## Decisions

### D1 新建「新闻和公告」模块，新闻和公告放在一起

新建 `modules/news/`，不改 `modules/marketdata`：行情是交易侧要的实时数值，不存状态库；新闻和公告是案件侧要的文字类事实，要存下、去重、按股票读。

新闻和公告放在一个模块：两者的用法一样（按股票和起始时刻取、按 ID 去重、返回新增的、按股票读），读它们的也是同一批（事件检测、状态库快照、Agent 网关）。

考虑过：

- 放进 marketdata：模块会同时管实时行情和文字存储，两种完全不同的约定
- 新闻、公告各一个模块：两份几乎一样的 service、ports 和 repo

### D2 两种条目放在 contracts

```python
class NewsArticle(BaseModel):
    """一条新闻，见 news-and-filings spec「新闻和公告的条目」。数据来自新闻源，不加约束。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int  # 新闻源给的编号
    headline: str
    summary: str
    content: str  # HTML；没有正文时为空
    author: str
    source: str  # 稿件来源，比如 benzinga
    url: str | None
    symbols: tuple[str, ...]  # 新闻源标注的股票
    published_at: AwareDatetime


class Filing(BaseModel):
    """一份 SEC 公告，见 news-and-filings spec「新闻和公告的条目」。数据来自公告源，不加约束。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accession_number: str
    symbols: tuple[str, ...]  # 问到的股票里属于这份公告所列公司的
    form: str  # 表单类型，原样
    items: tuple[str, ...]  # 8-K 的 item；别的表单为空
    accepted_at: AwareDatetime
    url: str  # 主文件地址
```

- 放 contracts：「事件检测」（一个模块）、「状态库快照」、Agent 网关都要读，模块之间只能通过包根或 contracts 传
- Pydantic：存成 JSON 再读回，`model_dump(mode="json")` 和 `model_validate` 原样往返
- 表单类型、item 用字符串，不做枚举：SEC 有上百种表单，事件规则只看其中几种，由「事件检测」匹配
- 不存的字段：新闻的 updated_at（不更新已存的行，见 D4）、images；公告的 CIK、filingDate、reportDate、主文件说明。需要时由以后的变更加

### D3 端口和用例

```python
class SourceUnavailableError(RuntimeError):
    """新闻源或公告源超时、断线、限频或出错：这次没有取到。"""


class NewsSource(Protocol):
    """新闻源。symbols 非空、不重复，由模块保证。失败时抛 SourceUnavailableError，不重试。"""

    def articles(self, symbols: Sequence[str], since: datetime) -> Sequence[NewsArticle]:
        """since（含）以来发布、标注了这些股票中至少一只的新闻，全部取完。"""
        ...


class FilingSource(Protocol):
    """公告源。symbols 非空、不重复，由模块保证。失败时抛 SourceUnavailableError，不重试。"""

    def filings(self, symbols: Sequence[str], since: datetime) -> Sequence[Filing]:
        """这些股票所属公司 since（含）以来受理的公告；查不到公司的股票没有公告。"""
        ...


class NewsStore(Protocol):
    """新闻和公告的存储：只追加，一条一行。数据库的错误原样抛出。"""

    def add_articles(self, articles: Sequence[NewsArticle]) -> tuple[NewsArticle, ...]:
        """写入还没存过的新闻（按 ID），给出这次真正写入的；已经存过的不写、不改。"""
        ...

    def add_filings(self, filings: Sequence[Filing]) -> tuple[Filing, ...]: ...

    def articles(self, symbol: str, since: datetime) -> tuple[NewsArticle, ...]:
        """标注了 symbol、发布时刻不早于 since 的，按发布时刻、ID 排序。"""
        ...

    def filings(self, symbol: str, since: datetime) -> tuple[Filing, ...]: ...


class NewsService:
    def __init__(self, news: NewsSource, filings: FilingSource, store: NewsStore) -> None: ...

    def fetch_articles(self, symbols: Iterable[str], since: datetime) -> tuple[NewsArticle, ...]:
        """股票去重；没有股票时返回空、不调用新闻源。取到的按 ID 去重（保留第一次出现的），
        交给存储，返回真正写入的，按发布时刻、ID 排序。"""

    def fetch_filings(self, symbols: Iterable[str], since: datetime) -> tuple[Filing, ...]: ...

    def articles(self, symbol: str, since: datetime) -> tuple[NewsArticle, ...]: ...

    def filings(self, symbol: str, since: datetime) -> tuple[Filing, ...]: ...
```

- 一个错误类型给两个数据源：调用方分开调用两个用例，从调用的是哪个就知道是哪个源；错误信息里带着请求
- "新增"由存储判断，不由 service 先读后比：同时取到同一条时只有真正写入的那个调用返回它（D4）
- 数据源失败时 service 不碰存储，也不捕获，原样抛出（proposal「需要人决定」第 5 条）
- 起始时刻由调用方给：「事件检测」只在取成功以后往前推（proposal「对后续变更的影响」）

### D4 两张一行一条的只追加表

```python
ARTICLES = Table(
    "news_articles",
    MetaData(),
    Column("id", BigInteger, primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("symbols", ARRAY(String), nullable=False),
    Column("data", JSONB, nullable=False),
    Index("news_articles_published_at", "published_at"),
)

FILINGS = Table(
    "news_filings",
    MetaData(),
    Column("accession_number", String, primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=False),
    Column("symbols", ARRAY(String), nullable=False),
    Column("data", JSONB, nullable=False),
    Index("news_filings_accepted_at", "accepted_at"),
)
```

`PostgresNewsStore(engine, clock)` 满足 `NewsStore`：

- 写入：一个事务、一条语句 `INSERT … VALUES (…), (…) ON CONFLICT (<键>) DO NOTHING RETURNING <键>`，返回的键就是真正写入的，按传入的顺序给出对应的条目。两个事务同时写同一个键时，后一个等前一个提交，然后什么都不做，RETURNING 里没有它，所以只有一个调用返回这一条。传入为空时直接给出空，不执行语句（空的 VALUES 不是合法的 SQL）
- 写入时刻：这次写入从时钟读一次，同一批的行相同
- 读取：`symbols @> ARRAY[symbol] AND published_at >= since ORDER BY published_at, id`（公告按 accepted_at、accession_number）
- `data` 存整份条目，`symbols`、时刻是从条目抄出来的筛选列；数据库的错误和读不出来的条目（Pydantic 的 `ValidationError`）原样抛出

不用 `VersionedStore`：

- 新闻和公告存下以后不变（新闻后来被改的不再写入，proposal「需要人决定」第 6 条），版本号永远是 1。和 Agent 网关的三张表一样一行一条，不存版本号（update-task-cost-storage「需要人决定」第 1 条人已同意）
- `VersionedStore.save` 一次一个键、先读后写，同时保存同一个键时后完成的报错；这里一批几十条，同时取到同一条是正常情况，要的是"不写、不报错、不算新增"

考虑过：

- 新闻被改了写一行新版本：「事件检测」要区分"新新闻"和"老新闻的新版本"，读的时候还要取最新版本；Benzinga 的改动多是补充细节
- 给 symbols 建 GIN 索引：每只股票每天几条到几十条，按时刻的索引先筛到最近一段，再逐行看 symbols 就够了

### D5 Alpaca 新闻适配器

```python
DATA_URL = "https://data.alpaca.markets"


class AlpacaNewsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    secret_key: SecretStr
    base_url: str = DATA_URL
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class AlpacaNewsSource:
    def __init__(
        self, config: AlpacaNewsConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        """transport 只给测试用：传入 httpx.MockTransport 代替真实的 Alpaca。"""

    def articles(self, symbols: Sequence[str], since: datetime) -> tuple[NewsArticle, ...]: ...
```

- 回复按 Pydantic 模型读（`extra="ignore"`，只取用到的字段），再转成 `NewsArticle`；`created_at` 是发布时刻
- 分页：每页 50 条（Alpaca 的上限），`sort=asc`；有 `next_page_token` 就带着它再请求，全部取完再返回。任何一页失败整次失败
- `start`：起始时刻换成 UTC、舍去秒以下，写成 `2026-10-14T12:00:05Z`；回来以后去掉发布时刻早于起始时刻的。这样不依赖 Alpaca 对秒以下部分的解析，也不依赖 start 按发布时刻还是更新时刻筛选
- 失败分类：`httpx.TransportError`（含超时）、429、5xx 抛 `SourceUnavailableError`；其余 4xx `raise_for_status()` 原样抛出；回复读不出来抛 Pydantic 的错误
- 单独的配置，不复用 `AlpacaConfig`：那里的地址是 Trading API 的，意思不同；key 是同一组，装配时从同样的两个环境变量读
- 不复用 `AlpacaClient`：它的失败抛执行模块的错误，换成新闻模块的错误要改它

### D6 SEC 的受理时刻：按实测的偏差换算

```python
NEW_YORK = ZoneInfo("America/New_York")


def _accepted_at(text: str) -> datetime:
    """submissions 的 acceptanceDateTime 比真实时刻晚了美东和 UTC 的时差，见 sec-filings spec。"""
    shown = datetime.fromisoformat(text)  # 2026-07-31T00:30:28.000Z
    return shown.astimezone(NEW_YORK).replace(tzinfo=UTC)  # 2026-07-30T20:30:28Z
```

- 依据：三份公告（夏令时的 8-K、10-Q、Form 4，冬令时的 8-K）的公告页面时刻都符合"submissions 的值 = 真实时刻 + 时差"；用 filingDate 也印证了：17:30 以后受理的普通公告会算到下一个工作日，而这几份按 submissions 的值换成美东都晚于 17:30、filingDate 却是当天
- 换算按 submissions 的值所在那天的时差；它和真实时刻那天的时差只在夏令时切换的那几个小时不同，那时 EDGAR 不受理
- 冒烟测试对照公告页面核对 0000320193-26-000018 的受理时刻，SEC 哪天修好了偏差会测出来，那时先改 spec 再改换算

考虑过：

- 直接按 Z 读：财报 8-K 会晚 4 到 5 小时，盘后发布的财报会被当作第二天的
- 用 filingDate：只有日期，17:30 以后受理的还算到下一个工作日
- 每份公告去读公告页面：每份多一次请求，页面是 HTML

### D7 SEC 适配器：代码表、请求间隔和合并

```python
class SecConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_agent: str = Field(min_length=1)
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))
    max_requests_per_second: int = Field(default=8, ge=1, le=10)


class SecFilingSource:
    def __init__(
        self,
        config: SecConfig,
        transport: httpx.BaseTransport | None = None,
        pause: Callable[[timedelta], None] = _wait,
    ) -> None:
        """transport、pause 只给测试用：替身 SEC，以及记下等待而不真的等。"""

    def filings(self, symbols: Sequence[str], since: datetime) -> tuple[Filing, ...]: ...
```

一次 `filings` 调用：

1. 代码表：内存里没有，或者有不在"查不到"名单里的股票查不到，就下载代码表（覆盖内存里的那份）；下载以后仍然查不到的股票记进"查不到"名单。股票代码的点换成横线再查
2. 按 CIK 分组，组内按调用方给出的顺序；每个 CIK 读一次 submissions
3. 从 `filings.recent` 按列配对（`zip(..., strict=True)`），换算受理时刻，留下不早于起始时刻的；items 按逗号分开；拼主文件地址
4. 按 accession number 合并，标注的股票按调用方给出的顺序合在一起

请求间隔：

- 一个 `threading.Lock` 包住整次 `filings` 调用：同一个进程里对 SEC 的请求不会同时发出，代码表和"查不到"名单也不会被两个调用同时改
- 每个请求结束以后（成功或失败）调用 `pause(timedelta(seconds=1) / max_requests_per_second)`，再发下一个。相邻两个请求的开始至少隔开这么久，所以任何 1 秒里不超过每秒最多请求数
- 生产用 `_wait`：`threading.Event().wait(秒数)`，不用 time 模块；测试传入记录等待的函数
- 默认每秒 8 次，留出余量：SEC 超过 10 次就封 10 分钟

考虑过：

- 按时钟算距离上一个请求过了多久，只在不够时才等：省掉每次 0.125 秒，但要注入时钟；一轮几十个请求多等几秒无所谓
- 不限速，靠网络延迟：data.sec.gov 有时几十毫秒就回来，几十家公司连着读会超过 10 次
- 每轮都下载代码表：800 KB 一次，轮询一天几百次

失败分类：`httpx.TransportError`、403、429、5xx 抛 `SourceUnavailableError`（带状态码和请求的地址）；其余 4xx 原样抛出；回复读不出来抛 Pydantic 的错误。403 也算不可用：SEC 限频和 User-Agent 不合要求都返回 403，分不出来；User-Agent 不对时每轮都失败，第一次部署跑冒烟测试就能发现。

`User-Agent` 放在 httpx 客户端的默认请求头里，所有请求都带。

### D8 迁移 v0008

`v0008_create_news_tables.py` 建两张表和两个索引，照 v0007 的写法写明每一列、不 import 业务代码；`revision = "0008"`，`down_revision = "0007"`；`downgrade` 照旧不支持。并行的「财报日历与板块」用 v0009，`down_revision = "0008"`。

### D9 测试

```
tests/fakes/news.py                         FakeNewsSource、FakeFilingSource、InMemoryNewsStore（新）
tests/modules/news/builders.py              news-and-filings spec 的基准新闻和公告（新）
tests/modules/news/test_service.py          用内存存储（新）
tests/modules/news/test_repo.py             PostgresNewsStore，要开着 OrbStack（新）
tests/integrations/alpaca/news_payloads.py  基准新闻的回复（新）
tests/integrations/alpaca/test_news.py      httpx.MockTransport（新）
tests/integrations/alpaca/test_news_smoke.py  news_sources 标记（新）
tests/integrations/sec/payloads.py          2026-10-17 从 SEC 录下的代码表和 AAPL submissions 的几行（新）
tests/integrations/sec/test_edgar.py        httpx.MockTransport（新）
tests/integrations/sec/test_edgar_smoke.py  news_sources 标记（新）
tests/bootstrap/test_database.py、test_app.py  表的清单、列、主键、索引、归属、最新版本（修改）
```

- 替身：两个数据源按调用依次给出安排好的结果，记下每次被问的股票和起始时刻，可以安排下一次抛 `SourceUnavailableError`；内存存储按键保存，写入和读取的规则和数据库实现相同
- `test_repo.py`：原样读回（进程在 UTC+14）；写入时刻；重启（同一个 engine 新建存储）；读写出错（连不上的地址、没升级的空数据库、手工写进一行缺标题的 data）；「同时取到同一条」用真的数据库：两个线程各自调用 `NewsService.fetch_articles`，替身新闻源在给出结果前用 `threading.Barrier` 等两个线程都到，然后两边同时写，断言表里一行、两边返回合起来正好一条
- 适配器测试：替身 SEC 按地址给出录下的回复，记下每个请求的地址、User-Agent 和先后；`pause` 换成记录函数，等待的时长从配置算出，不在测试里写死；「两个调用同时取」用两个线程，替身在处理请求时检查没有别的请求正在处理
- `source: NewsSource = AlpacaNewsSource(...)`、`source: FilingSource = SecFilingSource(...)` 让 mypy 检查适配器满足端口
- 冒烟测试，`news_sources` 标记，默认不运行（`addopts` 加 `and not news_sources`）：
  - Alpaca：没有 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY` 时跳过。取 AAPL、NVDA、TSLA 最近 7 天的新闻：超过 50 条（走过了分页），ID 不重复，发布时刻都不早于起始时刻，每条都标注了其中至少一只，至少一条有正文（免费档能拿到正文）
  - SEC：没有 `SEC_USER_AGENT` 时跳过。取 AAPL 自 2026-07-30 的公告：有 0000320193-26-000018，受理时刻 2026-07-30 20:30:28Z（和公告页面一致），item 是 2.02、9.01，主文件地址打得开；BRK.B 有公告；GOOGL、GOOG 一起取时每份都标注两只；ZZZZQQ 不报错
- 每个测试的说明写明对应的 spec 场景；配置的默认值不写测试（测试约定）

### D10 完整版 Proposal 的两处修改

apply 时按 proposal「What Changes」最后一项的措辞改「技术栈与部署」的数据源一行和「架构 · 触发」的数据源一句，只改这两处。

## Risks / Trade-offs

- [SEC 修好了受理时刻的偏差] → 换算出的时刻会早 4 到 5 小时；冒烟测试对照公告页面能发现，部署新版本前跑一次
- [User-Agent 填错] → 每次都报数据源不可用；错误信息带 403，第一次部署跑冒烟测试
- [Alpaca 免费档以后不给正文或收紧频率] → 正文为空照样能存；频率由「事件检测」的轮询间隔控制
- [submissions 体积] → 大公司压缩后几十 KB，五十家一轮几 MB；轮询间隔按分钟级，由「事件检测」定
- [一个锁串起全部 SEC 请求] → 一个调用卡在超时上时，另一个调用要等它；现在只有「事件检测」一个调用方
- [新闻正文占地方] → 估计一年 1 到 2 GB，见 proposal「需要人决定」第 3 条

## Migration Plan

- 迁移 v0008 建两张空表和两个索引，启动时自动升级；本变更以后没有写入方，表一直是空的，直到「事件检测」装配
- 按 `deploy/README.md`「更新」部署，更新前照常手动备份
- 回退：回到上一个提交，用更新前的备份恢复状态库
