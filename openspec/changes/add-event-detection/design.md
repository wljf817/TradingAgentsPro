# Design

## Context

现状：

- 排队运行（`workflows/queue_runner.py`）的 `QueueRunner.enqueue(trigger, triggered_at)` 可以从任何线程调用；没有任何触发源调用它
- 调度器的触发：`EventTrigger(symbol, category, holding_risk)`、`SellTrigger(symbol)`；排队表把触发存成带种类的 JSONB（`modules/scheduler/repo.py`）。`contracts/event.py` 只有 `EventCategory`，注释写着事件的完整格式由 M4 的事件检测定，止损单成交不是事件、开的是卖出复盘
- 案件上下文 `CaseContext.event` 只是 `EventCategory`（`workflows/case_flow.py` 的 `context`），替身按它判断入场条件满足（`integrations/standin/conclusions.py`）
- 新闻和公告模块的 `fetch_articles`、`fetch_filings` 存下还没存过的、返回新存下的，可是没有调用方；快照（`workflows/snapshot_flow.py`）只读已经存下的，所以快照里一直没有新闻和公告
- 公司数据模块的 `refresh_earnings` 也没有调用方（收盘心跳会接），`releases(symbol)` 读状态库
- 行情模块的 `Quote` 有最新价、前一交易日收盘价、更新时刻，没有成交量和量比；moomoo 适配器用 `get_market_snapshot`，成交量为 0 时没有最新价
- 执行模块的 `account()` 给出每只持仓的紧急阈值和今天开仓成交的均价；交易记录存储有 `traded()`（累计成交大于 0 的全部交易记录）
- 已知的行情问题（moomoo-quotes 冒烟测试 4.2，2026-10-15）：开盘前 OpenD 快照的常规时段字段还是上一个交易日的（最新价和成交量是前一天的，昨收落后一天）；什么时候切到当天还不知道，2026-10-19 开盘前后的录制还没分析。add-moomoo-quotes 还没归档，等分析完修它的 design D5。行情的量比也并进 add-moomoo-quotes（它的 design D9、tasks 6.1）
- 迁移到 v0013

动机见 proposal.md，要求见 specs/。

### 模块边界

```
contracts/
  event.py          (改) Event、EventSource、EventOutcome；EventCategory 不变
  agent_call.py     (改) CaseContext.event: Event | None
  snapshot.py       (改) EntryKind.EVENT、EventEntry、Snapshot.events
modules/events/     (新，对应架构图的「事件检测层」)
  __init__.py       导出 EventService、EventStore、PostgresEventStore、EventRulesConfig 和 domain 的判断函数
  config.py         EventRulesConfig、KeywordRule、FilingTiers
  domain/
    price.py        价格异动、成交量异动、行情要是当天的
    news.py         新闻的初筛、关键词匹配、没成为事件的原因，新闻和公告共用的级别判断
    filings.py      公告的初筛
    earnings.py     预计发布时刻、财报日历这一路、8-K 的财报发布事件、财报发布的间隔的起点
    ids.py          事件 ID、检测时刻和它的交易日
    text.py         数值、比例、级别和发布时段的写法
    entries.py      程序日志的说明：检测到事件、丢弃事件
  ports.py          EventStore
  repo.py           events_detected 表，PostgresEventStore
  service.py        EventService：记下、丢弃、读取
modules/scheduler/  (改) EventTrigger.event_id、SellTrigger.order_id；QueueStore.sales、QueueService.sales
modules/execution/  (改) ExecutionService.sales(since)
modules/programlog/domain/entry.py  (改) Actor.EVENT_DETECTION，Action.DETECT_EVENT、DISCARD_EVENT
integrations/standin/conclusions.py (改) 读 context.event.category
workflows/
  event_detection.py (新) EventDetector、EventDetectionConfig、DetectionParts、CaseIntake
  queue_runner.py    (改) QueueRunner.queued_sales
  case_entries.py    (改) 触发原因带事件 ID、客户端订单 ID
  case_flow.py       (改) 案件上下文读事件
  snapshot_flow.py   (改) 快照带事件
bootstrap/app.py     (改) 装配、开始和停止的顺序；Externals 加新闻源和公告源
bootstrap/migrations/versions/v0014_create_events.py (新)
```

- 跨模块传递的 Pydantic 模型：`contracts.event.Event`（事件模块产出；快照、案件上下文、程序日志的文字读它）。其余是已有的类型：调度器的 frozen dataclass 触发、行情的 `Quote`、执行模块的 `Account`、`HeldPosition`、`Trade`，新闻的 `NewsArticle`、`Filing`，公司数据的 `EarningsRelease`，股票池的 `PoolEntry`
- 事件模块只依赖 contracts、shared（`TradingCalendar`）、程序日志和状态库的 `connect` 写法；domain 只用白名单里的 `re`、`decimal`、`datetime`，纯区域白名单不变
- `workflows/event_detection.py` 只 import 各模块的包根和同层的 `case_entries`（不 import `queue_runner`：排队经 `CaseIntake` 协议交进来，`QueueRunner` 满足它）

## Goals / Non-Goals

**Goals:**

- 规则全是 domain 里的纯函数：给定股票状态、行情、新闻、公告、财报日历、已有事件和配置，判断出事件或没成为事件的原因；运行、读数据、记下、排队在 workflow 里
- 事件只有一份：事件表；事件 ID 就是去重的键，主键冲突就是重复，重启以后不用另外恢复状态
- 数据源各自失败，互不拖累：一部分读不到只跳过这一部分

**Non-Goals:**

- 不订阅 moomoo 推送、不设到价提醒（D5）
- 不刷新财报日历（收盘心跳）
- 不给卖出复盘算收益（M6）

按「编程风格」省略的情况：

- 进程在记下事件和排进案件之间退出：这个事件记下了，没有案件
- 进程在新闻存下和判断之间退出：这条新闻不再判断，快照里仍然有它
- 停机超过回看时长：期间的新闻、公告、财报发布和卖出不再成为事件，卖出也不复盘
- 紧急退出刚卖完、股票池还没跟上时这一轮把它当持仓、下一轮当候选：当天已经记下价格下跌事件的不受影响；没记下的（跌幅直接越过紧急阈值、事件检测这一轮还没看到）可能多开一个候选股事件
- 一轮之内股票池的状态变了：按这一轮开始时读到的
- 财报的发布日是周末：按之后第一个交易日（spec 已写），不另外处理节假日前后的时段
- 关键词误判（比如 approval 出现在 shareholder approval 以外的地方）：改关键词表
- 8-K 没有 item：按"其他 8-K"算一般
- 停牌、半天交易日：按交易日历和行情源给的
- 一次超过 400 只股票的行情请求：股票池远小于这个数
- 一条新闻的标注里同一只股票出现两次：只判断一次（新闻和公告模块原样存）

## Decisions

### D1 新的事件模块，规则是纯函数

事件检测层是架构图里单独的一个节点，Proposal 的状态库表里"事件"的写入者也是它，所以新建 `modules/events/`，不放进调度器、新闻或行情模块：

- 调度器只管"什么时候做什么"，不读行情和新闻
- 新闻和行情模块是数据层，不判断

domain 的函数签名（示意）：

```python
def detection(at: datetime, calendar: TradingCalendar) -> Detection: ...
def price_event(
    stock: PricedStock,
    reading: Reading,
    at: Detection,
    calendar: TradingCalendar,
    config: EventRulesConfig,
) -> Event | None: ...
def volume_event(
    stock: PricedStock,
    reading: Reading,
    at: Detection,
    calendar: TradingCalendar,
    config: EventRulesConfig,
) -> Event | None: ...
def judge_article(
    article: NewsArticle, stock: Monitored, at: Detection, config: EventRulesConfig
) -> Event | Discard: ...
def judge_filing(
    filing: Filing, stock: Monitored, at: Detection, config: EventRulesConfig
) -> Event | Discard: ...
def expected_release(
    day: date, time: EarningsTime | None, calendar: TradingCalendar, config: EventRulesConfig
) -> datetime: ...
def release_event(
    release: CalendarRelease,
    at: Detection,
    since: datetime,
    calendar: TradingCalendar,
    config: EventRulesConfig,
) -> Event | None: ...
def earnings_window_start(at: datetime, calendar: TradingCalendar, sessions: int) -> datetime: ...
def event_id(symbol: str, day: date, kind: EventKind) -> str: ...
```

- `Detection`：这一轮开始时读的时刻和它归属的交易日，事件 ID 的交易日、检测时刻都取它
- `PricedStock`：股票、状态、持仓的紧急阈值和今天开仓成交的均价（不是持仓时为 None）
- `Reading`：最新价、前一交易日收盘价、量比、更新时刻
- `Monitored`：一只监控股票、它这一轮的状态和加入股票池的时刻（判断加入以前发布的新闻和公告）
- `CalendarRelease`：财报日历上一次发布的股票、财年季度、发布日和发布时段
- 这些都是 domain 的 frozen dataclass。domain 是纯区域，只 import contracts 和 shared：行情的 `Quote`、执行模块的 `HeldPosition`、公司数据的 `EarningsRelease`、股票池的 `PoolEntry` 由 workflow 取出数值传进来；发布时段用 contracts 里已有的 `EarningsTime`（取值和公司数据的 `ReleaseTime` 相同）
- 新闻和公告判断出的不是事件时是 `Discard`，带 `DiscardReason`；8-K 的 item 2.02 直接给出类别是财报发布的事件。要读已经记下的事件的规则（当天已有、财报发布的间隔）由 workflow 判断，原因用同一个 `DiscardReason`
- "现在"作为参数传入

考虑过：把规则写在 workflow 里。规则表多、阈值边界多，纯函数才能逐行测。

### D2 事件的格式

```python
class EventSource(StrEnum):
    QUOTES = "quotes"  # 行情
    NEWS = "news"  # 新闻源
    FILINGS = "filings"  # 公告源
    EARNINGS_CALENDAR = "earnings_calendar"  # 财报日历


class EventOutcome(StrEnum):
    QUEUED = "queued"  # 排队
    EMERGENCY_EXIT = "emergency_exit"  # 交给紧急风险退出，不排队


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    symbol: str
    category: EventCategory
    holding_risk: bool
    source: EventSource
    strength: Decimal | None  # 涨跌幅、量比、级别 1 或 2；财报发布没有
    occurred_at: AwareDatetime
    detected_at: AwareDatetime
    summary: str  # 中文说明
    refs: tuple[str, ...]  # 新闻 ID、accession number、财报发布的键
    outcome: EventOutcome
```

- 类别直接用 `EventCategory`，不另设"种类"枚举：调度器按类别定事件要求的子 agent；价格上涨和下跌的区别只在事件 ID 和说明里。入场条件满足、达到目标价或证伪条件触发的事件由「论点条件与到价提醒」产生，格式相同，测试现在就能造出这两类事件
- 来源用抽象的名字（行情、新闻源……），不写 moomoo、Benzinga：数据源只在启动时决定
- 不存阈值：说明里写明；程序日志也从说明取
- 放在 contracts：快照、案件上下文和事件模块都用它，存下的快照要长期读得回来

### D3 事件 ID 是可读的去重键

`<股票>:<交易日>:<种类>`，种类是 price_up、price_down、volume、news、news_risk、earnings（event-rules spec「事件 ID 和去重」）。

- 同一个键只能记下一次：表的主键就是事件 ID，`INSERT ... ON CONFLICT DO NOTHING RETURNING`，返回有没有写入。去重、重启以后不重复、两个调用同时写，都由数据库保证
- 交易日用检测时刻归属的交易日，和 Proposal"案件侧按天计的上限按交易日算"一致：收盘以后的新闻算下一个交易日
- 催化剂和跟进引用事件时写事件 ID（比如 `BBB:2026-10-14:news`）；快照里的条目 ID 是 `s1/event/<事件 ID>`
- 财报发布的"5 个交易日一个"由 domain 查这只股票最近的事件判断（`EventService.events(symbol, window_start)`），不靠主键

考虑过：随机 UUID 加一张"去重键"表。多一张表，读起来也不知道是哪类事件。

### D4 卖出复盘不当作事件，按排队表去重

Proposal 把止损单成交列在事件来源里，但它开的是卖出复盘（Reviewer），不是给 PM 的事件案件；M3 已经把它做成单独的 `SellTrigger`。所以：

- 来源：交易记录。执行模块新增 `sales(since: date) -> tuple[Trade, ...]`：`traded()` 里类型是卖出或止损单、状态是全部成交、已撤销或已过期、成交日不早于 since 的，按成交日、客户端订单 ID 排。所有卖出（止损单、紧急退出和本地备用止损、PM 的减仓和清仓、`/plan`）都在交易记录里，重启以后也查得到
- 触发：`SellTrigger(symbol, order_id)`，`order_id` 是交易记录的客户端订单 ID；M6 的 Reviewer 按它找到这笔卖出
- 去重：`QueueStore.sales() -> frozenset[str]` 读排队表里种类是 sell 的触发的 `order_id`（JSONB 的 `trigger->>'order_id'`），出没出队都算；事件检测只排不在里面的
- 回看：成交日不早于回看起点归属的交易日，第一次部署不会给 M2 测试留下的旧卖出排复盘

考虑过：

- 卖出也记成事件：要给 `EventCategory` 加一类，调度器的子 agent 表要排除它；快照的"已发生的事件"里混进交易，PM 已经能从账户和以后的查询工具看到
- 在下单流程、紧急退出流程、巡检各自成交的地方排：三处都要改，重启和对账补上的成交还要另想办法

### D5 自己的线程，快照轮询

`EventDetector` 和 `QueueRunner` 的写法相同：守护线程 `event-detection`，开始以后立即做第一轮，每轮做完等 60 秒；停止时做完这一轮；一轮出错写 ERROR，下一轮照常。不挂在巡检上：巡检每 15 秒一轮、拿着全程序的锁跟订单，SEC 和新闻的请求会拖慢它。

行情用 `get_market_snapshot` 轮询，不订阅推送：

- 频率限制：快照每 30 秒 60 次。巡检里的紧急退出和健康判定每 15 秒各一次，事件检测每 60 秒一次，加起来远低于上限；一次最多 400 只股票，股票池远小于这个数
- 订阅额度（100）留给「论点条件与到价提醒」：到价提醒和推送在那里做
- 阈值是 4% 到 8% 的日内涨跌，一分钟的延迟不影响；紧急退出仍是巡检里每 15 秒一次
- 推送的回调在 SDK 的线程里，要加锁、处理断线重订阅；轮询没有这些

间隔：新闻每轮取（Alpaca 每分钟 200 次，一轮最多几页）；公告每 5 分钟取（SEC 一只股票一个请求，适配器限速每秒 8 次，30 只股票约 4 秒）；财报日历每轮只读状态库。

### D6 新闻和公告由事件检测来取

新闻和公告模块的"取"本来就是给事件检测用的（news-and-filings spec 的 Purpose）。每轮取全部监控股票从"检测时刻减 24 小时"以来的，`fetch_*` 返回新存下的，只判断这些：

- 重叠取不会重复判断：存储按 ID 去重，已经存下的不返回
- 断线、限频、重启以后自动补上：回看 24 小时覆盖了没取到的那段
- 第一次部署时会一次性判断最近 24 小时的；每只股票每天每组最多一个事件，开出的案件有限
- 加入股票池以前发布的不判断（`PoolEntry.added_at`）：寻股刚加进来的候选不会因为当天早些时候的新闻马上开候选股事件
- 快照从此有新闻和公告：取下来的不论成没成为事件都存着

考虑过：记一个"上次取到哪里"的时刻。要持久化、要处理新闻源晚到的稿件；回看加去重更简单。

### D7 不用模型的新闻强度

强度只有两级：一般 1、重大 2，取标题命中的关键词里最高的；命中的关键词里有负面的就是负面的。初筛的顺序和关键词表见 event-rules spec「新闻的初筛」「新闻关键词」。

- 关键词按"词的开头"匹配：正则 `(?<![0-9a-z])` 加关键词，不分大小写。acqui 能命中 acquire、acquisition，sues 不命中 issues
- 只看标题：Benzinga 的摘要常常为空，正文是 HTML，标题已经写明事件
- 标注的股票超过 2 只的是综述稿（"X stocks moving in Tuesday's session"）
- 配置：`KeywordRule(keyword: str, level: Literal[1, 2], negative: bool)` 的元组，默认表在 `config.py`
- 每种状态的最低级别（持仓和关注一般、候选重大）是 spec 的规则，写在 domain 里，不做成配置；新闻和公告共用这一条判断（`screened_event`）

考虑过：按命中个数打分、按来源加权。没有数据支持权重，等 M6 评估再说。

### D8 公告的分级

按 event-rules spec「公告的初筛」的表，`FilingTiers` 配置写明各类表单和 item。和 10-16 的规则相比：

- 不算 424B2：银行几乎每天交 424B2 发结构性票据，算进来持有银行股就天天有事件
- 增发和破产、减值、退市通知、财务报表不可依赖这几类 8-K 是负面的，持仓遇到是持仓风险级别

SEC 改了 13D 的表单名（SC 13D → SCHEDULE 13D），两种写法都列。

### D9 财报发布：8-K 的 item 2.02 和财报日历，先到的算

- 8-K 的 item 2.02 是公司发布财报的正式公告，通常发布后几分钟就被 SEC 受理；公告每 5 分钟取一次
- 外国公司（ADR）交 6-K，没有 8-K：靠财报日历。预计发布时刻按发布时段：盘前是开盘时刻，盘中是收盘时刻，盘后和没写是收盘后 60 分钟（给 8-K 留时间，8-K 先到就不用等）
- 同一只股票 5 个交易日内只有一个：8-K 和日历是同一次发布的两个来源；修正的 8-K/A 也不再开
- 财报日历要有人刷新：现在没有调用 `refresh_earnings` 的地方，按 M4 的安排由「收盘心跳」每天收盘刷新股票池里的股票。在那以前日历是空的，这一路不起作用，财报发布只靠 8-K
- 案件里看到的财报实际值：快照的财报条目是状态库里的版本，要等刷新才有实际值；8-K 本身只有元数据和链接。PM 主要从当天的新闻看结果。需要时以后在开财报案件前刷新一次这只股票（不在本变更）

### D10 价格和成交量：行情要是当天的

规则见 event-rules spec「价格异动」「行情要是当天的」「成交量异动」。

- 参考价和紧急退出相同（今天有开仓成交时用成交均价），持仓风险的阈值是这只持仓的紧急阈值乘 0.5：紧急阈值是 PM 按股票设的，持仓风险跟着它
- 跌幅达到紧急阈值的记下事件、处理是交给紧急风险退出、不排队：Proposal 规定紧急风险事件不开案件；记下它是为了当天这只股票清仓变成候选以后不再按候选的阈值开一次价格下跌事件
- 用乘法比较（`last <= ref * (1 - t)`），和 `emergency.drop_triggered` 一样，Decimal 的边界不受舍入影响；强度另外算涨跌幅，四舍五入到 4 位

**依赖开盘切换的修正**：开盘前 moomoo 快照的最新价、成交量是上一个交易日的，昨收落后一天，更新时刻是盘前的时刻。这一层用"行情的更新时刻不早于当天开盘、并且有最新价"挡住开盘前的旧行情：开盘以前更新时刻一定早于开盘。挡不住的是"开盘以后更新过、但 OpenD 还没把常规时段的字段切到当天"的那段，那时算出来的是前一天的涨跌。修好要靠 add-moomoo-quotes 按 2026-10-19 的录制修正它的 D5（切换以前不给最新价）；如果切换在开盘以后一段时间才完成，改的是它的映射，这里的规则不用改。这只是运行上的依赖，不挡 apply：部署到主机以前要先修好。没修好时的最坏后果：前一天涨跌超过阈值的股票，开盘后切换前的那几分钟按前一天的涨跌多开一个事件案件，每只股票每天最多一个

成交量用行情的量比（moomoo 快照的 `volume_ratio`：当天平均每分钟成交量和过去 5 日平均每分钟成交量之比），已经按一天里的时间归一，开盘 30 分钟以后才判断。`Quote.volume_ratio` 由 add-moomoo-quotes 加（它的 design D9），本变更 apply 时先做它的 tasks 6.1，字段名在那里用真实 OpenD 核对。

### D11 去重和一轮里的先后

见 event-rules spec「事件 ID 和去重」、event-detection spec「一轮」。实现上：

- 价格、成交量：算出事件就记下，主键冲突就是"当天已有"，不写日志（价格每分钟读一次）
- 新闻、公告：先查当天有没有 `<股票>:<交易日>:earnings`，有就丢弃；再记下，主键冲突就丢弃、原因是当天已有新闻或公告事件
- 财报发布：先查这只股票窗口内有没有类别是财报发布的事件
- 一轮里公告和财报日历在新闻之前，同一轮到的 8-K 先占住当天

### D12 记下、写日志、排队；触发时刻

```python
class CaseIntake(Protocol):
    def enqueue(self, trigger: Trigger, triggered_at: datetime) -> UUID: ...
    def queued_sales(self) -> frozenset[str]: ...
```

每个事件：`EventService.record(event, status)` 写入成功时写检测到事件，返回 True；处理是排队的再 `intake.enqueue(EventTrigger(symbol, category, holding_risk, event_id), detected_at)`。先记下再排队：排队失败（数据库出错）时事件已经在，下一轮不会重复排，这个事件没有案件（省略清单）；反过来先排队，失败时会排两次。

触发时刻用检测时刻（这一轮开始时读的时刻），不用发生时刻：

- 队列"同优先级按触发时间先后"，检测时刻和入队的先后一致；发生时刻来自各数据源，SEC 的受理时刻和新闻的发布时刻都可能比检测早几个小时（停机以后补上的）
- 发生时刻存在事件里，PM 看得到

触发原因（`case_entries.case_trigger`）带上 ID：`BBB 的新闻事件（BBB:2026-10-14:news）`、`CCC 卖出成交（CCC-STOP-1）`。

### D13 事件进快照和案件上下文

- 快照：针对单只股票的案件另带这只股票检测时刻不早于新闻回看起点的事件（默认 5 个交易日），条目 `s<v>/event/<事件 ID>`，排在 surprise 之后。Proposal 的 Shared State 有"已发生的事件"；只带案件股票的，和新闻、公告一样。触发这个案件的事件一般在里面（排队超过 5 个交易日的除外，它在案件上下文里）
- 案件上下文：`CaseContext.event: Event | None`，`case_flow.context` 按触发里的事件 ID 读 `EventService.event`；读不到时 assert 失败（先记下再排队，读不到只能是程序错了）。Proposal 的案件上下文写的是"触发原因和事件内容"
- 替身：`context.event is EventCategory.ENTRY` 改为 `context.event is not None and context.event.category is EventCategory.ENTRY`，行为不变
- 快照的格式加一项：`Snapshot.events` 没有默认值。主机还没部署过 v0011 以后的版本，没有存着的快照；本地开发库里旧的快照读不回来时清掉

### D14 程序日志

行为者 `EVENT_DETECTION`（事件检测层），动作 `DETECT_EVENT`（检测到事件）、`DISCARD_EVENT`（丢弃事件），由 `EventService` 写（每个模块写自己的行为者，和股票池一样）。文字在 `domain/entries.py`（它要读 `news.py` 的 `DiscardReason`，放进 `text.py` 会和 `news.py` 互相 import）：检测到事件写类别、股票和状态、是否持仓风险级别、来源、强度、说明、处理；丢弃事件写标题或表单和 item、原因。

### D15 装配

- `EventService(PostgresEventStore(engine), log)`；`EventDetector(DetectionParts(pool, execution, market_data, news, company_data, events, intake=queue, clock, calendar, rules=EventRulesConfig()), EventDetectionConfig())`
- 快照流程和案件工作流多拿一个 `EventService`；案件那一侧装配好的新闻和公告、公司数据、事件交给事件检测
- `Externals` 加 `news`、`filings` 两个工厂（生产是 `AlpacaNewsSource`、`SecFilingSource`）：事件检测在运行中真的取新闻和公告，启动的测试要换成替身，不能连 Alpaca 和 SEC
- 启动：排队运行开始以后开始事件检测，再开始盘中巡检；停止：巡检 → 事件检测 → 排队运行
- 不用全程序的锁：只读执行模块（`account()`、`sales()`），和快照流程一样

### D16 迁移 v0014

`events_detected`：`event_id`（主键，字符串）、`symbol`（字符串，加索引）、`detected_at`（带时区）、`event`（JSONB，`Event` 按 json 模式存，读回时按 `Event` 校验）。只追加，没有更新和删除。

### D17 测试

- `tests/modules/events/domain/`：每张规则表一行一个测试，阈值两侧从配置读；关键词默认表的每一行；预计发布时刻的四种时段和周末；事件 ID 和交易日
- `tests/modules/events/test_service.py`（内存存储 `tests/fakes/events.py`）、`test_repo.py`（OrbStack）：原样读回、主键冲突、并发、读写出错
- `tests/workflows/test_event_detection.py`：event-detection spec 的场景，行情、新闻、公告、交易记录、队列都用 fakes，`FakeClock`；线程的开始、间隔、停止照 `test_queue_runner` 的写法
- 调度器、快照、案件工作流、替身、交易记录、排队运行、启动的已有测试跟着改：`EventTrigger`、`SellTrigger` 的新字段，`CaseContext.event` 的类型

## Risks / Trade-offs

- [开盘后、OpenD 切换以前的旧行情] → 见 D10：更新时刻挡住开盘前的，其余靠 add-moomoo-quotes 的修正，部署到主机以前修好；没修好时最坏是前一天涨跌超过阈值的股票开盘后多开一个案件，每只每天最多一个
- [关键词表粗糙，漏报和误报都会有] → 可配置；每条丢弃事件都写了原因，M6 按命中率改
- [事件案件吃预算] → 每只股票每天每组最多一个；候选只认重大事件；普通预算用完时非持仓案件留在队列里，持仓案件用保留预算（已有规则）
- [SEC 的流量：每 5 分钟每只股票一次 submissions，大公司的 JSON 有几百 KB] → gzip 以后每天几百 MB；不够用时调大公告间隔，或者换成 SEC 的 latest filings 列表（不在本变更）
- [财报日历没人刷新，财报发布只靠 8-K] → 收盘心跳接上以后才完整；ADR 在那以前没有财报发布事件，靠新闻
- [事件检测线程卡住没人发现] → 健康监控不检查它（Proposal 的五项检查里没有）；程序日志和 Python 日志里看得出来。需要时以后加心跳

## Migration Plan

- apply 时先做 add-moomoo-quotes 的量比任务（它的 tasks 6.1）并勾掉；add-moomoo-quotes 不用先归档
- 部署到主机以前，add-moomoo-quotes 的开盘切换修正要已经部署（D10）
- 迁移 v0014 建 `events_detected`；回滚只 revert 代码，表留着（迁移只升级）
- 部署前确认 `dbos.workflow_status` 里没有 PENDING 的 `case`：触发和案件上下文的格式变了
- 部署以后第一轮会判断最近 24 小时的新闻和公告，每只股票当天可能开出几个事件案件；卖出只看回看起点以后成交的
- 运维：盯几天程序日志里的检测到事件和丢弃事件，看阈值和关键词是否合适
