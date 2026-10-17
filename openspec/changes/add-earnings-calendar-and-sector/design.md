# Design

## Context

现状：

- 板块只是一个字符串：`Listing.sector`、`PoolEntry.sector`、`TradePlan.sector`、`contracts/account.Position.sector` 都是 `str`，风控的板块集中度按字符串相等分组（`risk/domain/exposure.sector_value`）。股票池的板块由调用方传入，项目里没有地方能查板块
- 外部数据源的写法：模块在 `ports.py` 定义端口和「不可用」异常，适配器放 `integrations/<名字>/`，只 import 模块的包根（`marketdata` + `integrations/moomoo`）。HTTP 适配器用同步的 httpx，测试传入 `httpx.MockTransport`，超时、连不上、429、5xx 归为不可用，其余原样抛出（`integrations/alpaca/client.py`）
- 状态库的写法：`modules/statestore` 的 `versioned_table` 和 `VersionedStore`，按键保存版本，`latest(**where)` 按筛选列取每个键的最新版本，`get(key)` 取一个键的最新版本；表放在拥有它的模块的 `repo.py`
- 迁移到 v0007；「新闻和公告」（add-news-and-filings）会先 apply，用 v0008
- 按日期范围取数据的先例：`bootstrap/app.py` 用 `(clock.now() + lookahead).date()` 算交易日历的范围边界，注明"范围的边界，不是交易日"
- 冒烟测试的写法：标记登记在 `pyproject.toml`，`addopts` 默认排除；缺环境变量时 `skipif` 跳过（`tests/integrations/alpaca/test_paper_smoke.py`）

Finnhub（2026-10-17 读 `https://finnhub.io/static/swagger.json`）：

- key 放在 `token` 参数或请求头 `X-Finnhub-Token` 里
- 限额：超过时返回 429；所有套餐另有每秒 30 次的上限。免费档每分钟 60 次来自 finnhub-js 的 issue 和第三方页面，官方定价页是脚本渲染的，没读到；有一个第三方页面另写了每天 300 次，未证实
- `/calendar/earnings`：免费档"1 month of historical earnings and new updates"；参数 from、to、symbol；返回 `{"earningsCalendar": [...]}`，每项 symbol、date、hour（bmo、amc、dmh）、year、quarter、epsEstimate、epsActual、revenueEstimate、revenueActual。EPS 和营收是 non-GAAP
- `/stock/earnings`：免费档"Last 4 quarters"；返回数组，每项 actual、estimate、surprise、surprisePercent、period、symbol、year、quarter
- `/stock/profile2`：免费；`finnhubIndustry` 是"Finnhub industry classification"，文档没有列出全部取值
- 文档示例里 `/stock/earnings` 的 period 和 year、quarter 与同一家公司 `/calendar/earnings` 的 year、quarter 对不上，两者的季度口径可能不同

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/companydata/                  (新)
  __init__.py        导出 SectorEtf、EarningsRelease、ReleaseTime、EarningsSurprise、SurpriseHistory、
                     CompanyDataSource、CompanyDataUnavailableError、EarningsStore、
                     PostgresEarningsStore、CompanyDataConfig、CompanyDataService
  domain/sector.py   SectorEtf、行业代码表、sector_of
  domain/earnings.py ReleaseTime、EarningsRelease、EarningsSurprise、SurpriseHistory
  ports.py           CompanyDataSource、CompanyDataUnavailableError、EarningsStore
  repo.py            表 companydata_earnings_releases、companydata_earnings_surprises；PostgresEarningsStore
  config.py          CompanyDataConfig
  service.py         CompanyDataService

integrations/finnhub/                 (新)
  __init__.py        导出 FINNHUB_URL、FinnhubConfig、FinnhubSource
  source.py          FinnhubConfig、FinnhubSource

bootstrap/migrations/versions/v0009_create_company_data.py   (新)
```

- 公司数据模块只依赖 `statestore` 的包根和 `shared` 的 Clock；`integrations/finnhub` 只依赖公司数据模块的包根
- 不新增、也不修改跨模块传递的 Pydantic 模型。`SectorEtf` 是 `StrEnum`，本身就是 `str`，「寻股粗筛」直接把它填进 `Listing.sector`；财报的类型是 domain 的 frozen dataclass，从包根导出
- contracts、shared、别的模块、workflows 和 bootstrap 的装配都不改

## Goals / Non-Goals

**Goals:**

- 「寻股粗筛」能用一个调用查到板块，板块的值能直接给风控分组、给 M6 取 ETF 行情
- 财报日历按「一次发布」保存，发布日推迟、实际值公布都是同一条记录的新版本，以后催化剂引用日程项时 ID 不变
- Finnhub 出问题时分清"以后再取"（数据源不可用）和"要人处理"（key、权限、格式）

**Non-Goals:**

- 按日期范围列出所有股票的财报（比如"今天发布的"）：调用方现在按股票读，以后需要时加筛选列和读取方法
- surprise 和财报日历按季度关联：两个接口的季度口径可能不同，各存各的
- 数据源返回的行业名的规范化，除了去首尾空白和不分大小写

按「编程风格」省略的情况：

- 一次发布在数据源里换了财年季度，或者从日历里消失：状态库里留着最后一个版本
- 发布日挪到窗口以外：这次结果里没有，保持原来的版本
- `hour` 是四种以外的值、季度不在 1 到 4、必有的字段缺失：解析报错原样抛出
- `earningsCalendar` 是 null 而不是空数组：解析报错，冒烟测试核对查不到的股票时的返回
- ADR 的营收按数据源给的货币保存，不换算
- 股票改代码：按新代码查，旧代码的记录留着
- 刷新时数据库在保存几条以后出错：前面的已经保存，这次报错；下次刷新补齐
- 两个调用同时刷新同一只股票：同一个键同时写同一个版本号，后完成的报错（state-store spec）
- profile2 的交易所、市值等其他字段不用：上市信息由「寻股粗筛」从别处取
- 每秒 30 次的上限：调用方按每分钟 60 次控制时碰不到
- HTTP 3xx：Finnhub 不重定向，按 httpx 默认不跟随，`raise_for_status` 报错
- 冒烟测试只核对一部分股票的板块，代码表里其余的行业靠以后的 WARNING 日志发现

## Decisions

### D1 新建公司数据模块

模块叫 `companydata`（公司数据）：两样数据都是按公司取的，和按价格取的 `marketdata` 并列。

- 不放进 `marketdata`：那个模块是交易侧的实时行情，没有存储；财报要进状态库，数据源也不同
- 不叫 `fundamentals`：和子 agent Fundamentals 重名，读代码时分不清是数据还是 agent
- 板块和财报放在一个模块里：同一个数据源、同一个端口，拆成两个模块就要两个端口共用一个适配器，没有好处

### D2 板块是 SPDR 板块 ETF 的代码

```python
class SectorEtf(StrEnum):
    """板块：11 个 SPDR 板块 ETF 之一，值就是 ETF 的代码。"""

    XLK = "XLK"  # 科技
    XLC = "XLC"  # 通信服务
    XLY = "XLY"  # 可选消费
    XLP = "XLP"  # 必需消费
    XLV = "XLV"  # 医疗
    XLF = "XLF"  # 金融
    XLE = "XLE"  # 能源
    XLI = "XLI"  # 工业
    XLB = "XLB"  # 材料
    XLU = "XLU"  # 公用事业
    XLRE = "XLRE"  # 房地产


def sector_of(industry: str) -> SectorEtf | None:
    """行业对应的板块；去掉首尾空白、不分大小写比较，表里没有时为 None。"""
```

代码表按 company-data spec「板块」的表，在 domain 里写成「ETF → 行业名的元组」，模块加载时展开成「`casefold` 后的行业名 → ETF」的 `MappingProxyType`。

- 板块的值用 ETF 代码，而不是中文名或 GICS 的板块名：风控只要能分组；M6 要拿 ETF 的行情算超额收益，用代码就不用再查一张表
- 不分大小写、去首尾空白：Finnhub 的行业名没有官方清单，大小写和空白记得不准（比如 "Consumer products" 的小写 p、"Energy " 末尾的空格），这样比较不改变含义，又少一类对不上
- 行业名按记得的 Finnhub 取值写，拿到 key 后由冒烟测试核对（D10、tasks 6.2）；对不上的先改 spec 的表，再改代码表

已知横跨两个板块的行业，按多数归一个（proposal「需要人决定」第 2 条）：

- Retail → XLY；沃尔玛、好市多、塔吉特、克罗格、Dollar General 在 XLP
- Building、Construction → XLI；住宅建筑商（D.R. Horton、Lennar）在 XLY，建材（Vulcan、Martin Marietta）在 XLB
- Communications → XLK（通信设备，比如思科）；如果 Finnhub 也把电信运营商放在这里，冒烟测试会发现

考虑过：

- 让适配器直接给出板块：代码表就成了 Finnhub 适配器的私有逻辑，没法在 domain 里测；人指定代码表放在 domain
- 用 SEC EDGAR 的 SIC 码对应：同样要一张对应表，SIC 比 Finnhub 的行业更细、更旧，数据源已经定为 Finnhub

### D3 端口

```python
class CompanyDataUnavailableError(RuntimeError):
    """数据源超时、连不上、限频或服务端出错：这次没有取到，以后再取。"""


class CompanyDataSource(Protocol):
    """公司数据的数据源。每次调用发一个请求，失败时抛 CompanyDataUnavailableError，不重试。"""

    def industry(self, symbol: str) -> str | None:
        """数据源给出的行业，原样；查不到这只股票或者没有行业时为 None。"""
        ...

    def earnings_calendar(self, symbol: str, start: date, end: date) -> Sequence[EarningsRelease]:
        """发布日在 start 到 end（含两端）之间的财报发布，顺序和数据源给的相同。"""
        ...

    def earnings_surprises(self, symbol: str) -> Sequence[EarningsSurprise]:
        """数据源给出的最近几个季度的 surprise，顺序和数据源给的相同。"""
        ...
```

端口给出原样的行业，对应板块在 domain 做（D2）。排序在模块里做（D6），适配器只管映射。

### D4 财报的类型

```python
class ReleaseTime(StrEnum):
    BEFORE_OPEN = "before_open"  # 盘前
    AFTER_CLOSE = "after_close"  # 盘后
    DURING_MARKET = "during_market"  # 盘中


@dataclass(frozen=True, slots=True)
class EarningsRelease:
    """一只股票的一次财报发布，见 company-data spec「财报的记录」。数据来自数据源，不加约束。"""

    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    day: date  # 美东日期
    time: ReleaseTime | None
    eps_estimate: Decimal | None
    eps_actual: Decimal | None
    revenue_estimate: Decimal | None
    revenue_actual: Decimal | None

    @property
    def key(self) -> str:
        """一次发布的键：股票代码和财年季度，比如 AAPL:2026Q4。"""
        return f"{self.symbol}:{self.fiscal_year}Q{self.fiscal_quarter}"


@dataclass(frozen=True, slots=True)
class EarningsSurprise:
    period: date  # 报告期，这个季度最后一天
    fiscal_year: int
    fiscal_quarter: int
    eps_actual: Decimal | None
    eps_estimate: Decimal | None
    surprise: Decimal | None
    surprise_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class SurpriseHistory:
    """一只股票最近几个季度的 surprise，按报告期从新到旧。"""

    symbol: str
    quarters: tuple[EarningsSurprise, ...]

    @classmethod
    def of(cls, symbol: str, surprises: Iterable[EarningsSurprise]) -> Self:
        """按报告期从新到旧排好。"""
        ...
```

- 键用股票代码加财年季度：发布日会推迟、预期每天在变，只有财年季度标识"哪一次发布"。以后催化剂引用财报日历的日程项，可以直接用这个键作 ID（`CatalystReference.ref_id` 现在不限定格式），由「状态库快照」或「案件工作流」决定
- surprise 整组一条记录，保存前排好序：Finnhub 换了返回顺序时内容不变，不会多写一个版本
- 数值都可以为空：未来的发布没有实际值，冷门股没有一致预期

### D5 存储：两张版本表

```python
RELEASES = versioned_table(
    "companydata_earnings_releases",
    Column("release_key", String, nullable=False),
    Column("symbol", String, nullable=False),
)
SURPRISES = versioned_table(
    "companydata_earnings_surprises",
    Column("symbol", String, nullable=False),
)
```

```python
class EarningsStore(Protocol):
    """财报的存储，数据库实现在 repo.py，见 state-store spec。错误原样抛出，不当作没有记录。"""

    def save_release(self, release: EarningsRelease) -> None: ...

    def releases(self, symbol: str) -> tuple[EarningsRelease, ...]:
        """这只股票每次发布的最新版本，顺序不保证。"""
        ...

    def save_surprises(self, history: SurpriseHistory) -> None: ...

    def surprises(self, symbol: str) -> SurpriseHistory | None:
        """最近一次保存的那一组；没有保存过时为 None。"""
        ...
```

`PostgresEarningsStore` 用两个 `VersionedStore`：`releases` 是 `latest(symbol=...)`，`surprises` 是 `get(symbol)`。

- 用版本表：财报数值是「当前市场事实」，Proposal 要求带版本号、只追加；发布日的变化留在旧版本里，以后复盘能看到财报推迟过
- 财报日历一次发布一个键，而不是一只股票一条记录装下整个窗口：催化剂引用的是一次发布；窗口移动时，移出窗口的发布不会因为整条记录被替换而消失
- 筛选列只有 `symbol`：现在只按股票读；按日期列出等有调用方时再加（Non-Goals）

### D6 用例

```python
class CompanyDataService:
    def __init__(
        self,
        source: CompanyDataSource,
        store: EarningsStore,
        clock: Clock,
        config: CompanyDataConfig,
    ) -> None: ...

    def sector(self, symbol: str) -> SectorEtf | None: ...

    def refresh_earnings(self, symbol: str) -> None: ...

    def releases(self, symbol: str) -> tuple[EarningsRelease, ...]: ...

    def surprises(self, symbol: str) -> SurpriseHistory | None: ...
```

- `sector`：取行业，`sector_of` 对应；行业不在代码表里时写 WARNING 日志（D9）。不保存：板块在加入股票池时写进记录，之后不变；每次查一次请求，量很小
- `refresh_earnings`：窗口 `start = (now - 往前天数).date()`、`end = (now + 往后天数).date()`，now 是 Clock 给的 UTC 时刻。先取日历、再取 surprise，两项都取到才保存（spec「刷新一只股票的财报」），这样不会只存了一半就让读的人以为是完整的
  - 窗口用 UTC 日期：它只是请求的范围，不是交易日；两端差一天，在 30 天和 90 天的窗口里没有影响。和 `bootstrap/app.py` 算交易日历范围的写法一样
  - 一次一只股票：调用方决定刷新哪些、什么节奏（D8）
- `releases`：按 `(发布日, 财年, 季度)` 排序；`surprises` 原样给出存储读到的那一组

配置：

```python
class CompanyDataConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lookback_days: int = Field(default=30, gt=0)
    lookahead_days: int = Field(default=90, gt=0)
```

- 往前 30 天：免费档只给 1 个月的历史；刚发布的那一次的实际值留在日历里
- 往后 90 天：催化剂截止日期最晚是 60 个交易日以后（约 87 天），90 天盖得住；下一次财报一般在 3 个月内

### D7 Finnhub 适配器

```python
FINNHUB_URL = "https://finnhub.io/api/v1"


class FinnhubConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr = Field(min_length=1)
    base_url: str = Field(default=FINNHUB_URL, min_length=1)
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class FinnhubSource:
    """满足 CompanyDataSource。"""

    def __init__(self, config: FinnhubConfig, transport: httpx.BaseTransport | None = None) -> None:
        """transport 只给测试用：传入 httpx.MockTransport 代替真实的 Finnhub。"""
```

- key 放请求头 `X-Finnhub-Token`，不放 `token` 参数：httpx 的错误信息和日志里有完整网址，放在头里就不会出现在错误信息和日志里
- 返回内容用 `extra="ignore"` 的 Pydantic 模型解析，`model_validate_json` 直接读 JSON 文本：Decimal 字段按 JSON 里写的数字精确读取（已在本机验证：`4.5474` 读成 `Decimal("4.5474")`，`88496400810` 读成 `Decimal("88496400810")`）。`hour` 是 `Literal["bmo", "amc", "dmh", ""] | None`，季度 `Field(ge=1, le=4)`
- `/stock/profile2` 的模型里 `finnhubIndustry` 默认 None：空对象和没有这个字段是 spec 写明的正常情况；空字符串也给 None
- 调用失败按 finnhub-company-data spec「调用失败的分类」：`httpx.TransportError`（含超时）、429、5xx 抛 `CompanyDataUnavailableError`，信息写 `GET 路径` 和原因；其余非 2xx 用 `raise_for_status()` 原样抛 `httpx.HTTPStatusError`；格式对不上的 `ValidationError` 原样抛出
  - 401、403 不归为不可用：key 填错或者接口不在免费档里，等多久都不会好，要人看到
- 不重试，理由同 Alpaca 和 moomoo：调用方会在以后再取

考虑过：用 Finnhub 的官方 Python SDK（finnhub-python）。它是 requests 写的、没有类型标注，错误都抛成同一种异常，分类反而要看文字；三个接口直接用 httpx 更简单，也和 Alpaca 适配器一致。

### D8 限频由调用方控制

适配器和用例都不等待、不计数：

- 等待只能用 DBOS 的持久化休眠（time 模块全项目禁用），属于以后的工作流
- 请求量：刷新一只股票 2 个请求，查板块 1 个。股票池最多 30 只加上持仓，一轮刷新约 80 个请求，超过每分钟 60 次，调用方要分批或在步骤之间休眠。这一点写进 proposal「对后续变更的影响」
- 偶尔超过时收到 429，报数据源不可用，调用方以后再取，不会出错

### D9 映射不到的行业写 WARNING 日志，不写程序日志

- Proposal 的程序日志按行为者列了要记的动作，没有数据层；行业对不上是代码表不全，属于 bootstrap spec「日志」里的"技术问题：取不到数据"
- 只在行业不在代码表里时写；查不到这只股票、没有行业时不写，调用方自己知道没有板块
- logger 用 `logging.getLogger(__name__)`，和 `modules/notification/service.py` 一样

### D10 迁移和测试

迁移 `v0009_create_company_data.py`：`revision = "0009"`、`down_revision = "0008"`，写明每一列，不 import 业务代码，`downgrade` 抛 `NotImplementedError`，和 v0005 一样。

测试：

- `tests/fakes/company_data.py`：
  - `FakeCompanyDataSource`：按股票保存行业、财报日历和 surprise；记录每次调用（方法和参数）；可以安排某个方法下一次抛 `CompanyDataUnavailableError`；日历按请求的日期范围筛选
  - `FakeEarningsStore`：按键保存每个版本（内容不同才追加），测试能看到一个键有几个版本
- `tests/modules/companydata/domain/test_sector.py`：「板块」的全部场景
- `tests/modules/companydata/domain/test_earnings.py`：键的写法、`SurpriseHistory.of` 的排序
- `tests/modules/companydata/test_service.py`：「查一只股票的板块」「财报的记录」「刷新一只股票的财报」「读取财报」和「公司数据配置」的改窗口；WARNING 日志用 `caplog`
- `tests/modules/companydata/test_config.py`：默认配置、配置非法
- `tests/modules/companydata/test_repo.py`（要开着 OrbStack）：「财报的存储」的三个场景，以及用真的数据库跑「同一个财年季度是同一次发布」
- `tests/integrations/finnhub/payloads.py`：spec 基准数据的三个 JSON，函数按需覆盖字段；`tests/integrations/finnhub/test_source.py`：`httpx.MockTransport` 记录请求、按路径返回，覆盖 finnhub-company-data spec 除配置以外的场景；超时由 transport 抛 `httpx.ReadTimeout`，连不上抛 `httpx.ConnectError`；`source: CompanyDataSource = FinnhubSource(...)` 让 mypy 检查适配器满足端口
- `tests/integrations/finnhub/test_config.py`：「Finnhub 配置」
- 冒烟测试 `tests/integrations/finnhub/test_finnhub_smoke.py`：加 `finnhub_api` 标记，没有 `FINNHUB_API_KEY` 时跳过。整个文件一共不超过 50 个请求，留在每分钟 60 次以内：
  - 11 个板块 ETF 各取 2 到 3 只大市值成分股（比如 XLK 的 AAPL、MSFT、NVDA），用真的适配器查板块，应当就是那个 ETF；不符的全部收集起来一起报，用来改代码表。D2 列出的已知横跨（沃尔玛等）单独断言它们的归属
  - 几只大市值股票在默认窗口里至少有一次发布，并且至少一只的发布日在 30 天以后：证明免费档给得出 1 个月以后的日历
  - AAPL 的 surprise 有 4 个季度
  - 查不到的 ZZZZQQ：没有行业、日历和 surprise 都是空的（核对 `{}`、`{"earningsCalendar": []}`、`[]` 的假设）
  - BRK.B 有行业：带点的代码 Finnhub 认得
  - 不经过适配器直接读一次 profile2，响应头 `X-Ratelimit-Limit` 是 60：核对免费档的限额

## Risks / Trade-offs

- [`finnhubIndustry` 没有官方的取值清单，代码表是按记得的取值写的] → 去首尾空白、不分大小写比较；拿到 key 后冒烟测试按各板块 ETF 的成分股核对；以后遇到对不上的有 WARNING 日志
- [免费档未来能给多远的日历、每天有没有总量限制，没有官方来源] → 冒烟测试核对；给不到 90 天时改配置默认值，有每天的上限时写进「对后续变更的影响」由调用方控制
- [Finnhub 改了免费档的范围] → 接口要付费时返回 403，原样报错，人能看到，不会被当成暂时不可用
- [按行业对应板块，少数股票的板块不准（D2 的横跨）] → 板块只用于集中度分组和 M6 的比较基准，归错一只只影响这一只；proposal「需要人决定」第 2 条
- [人还没有 Finnhub 的 key] → 除冒烟测试外都用替身；tasks 6.2 等拿到 key 后再做，没做完不归档

## Migration Plan

- 新增迁移 v0009，建两张空表；启动时随其他迁移一起升级
- 没有新依赖；没有装配，部署机器上不需要 key
- 回滚：revert 代码；两张表留在数据库里没有影响（迁移只升级，见 add-state-store 的 design D6）
