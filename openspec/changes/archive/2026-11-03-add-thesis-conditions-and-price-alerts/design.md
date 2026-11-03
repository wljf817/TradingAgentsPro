# Design

## Context

现状：

- 论点的条件（`contracts/thesis.py`）：`Condition(kind, comparison, value)`，`kind` 是价格（最新价）、涨跌幅（相对前一交易日收盘价的比例，下跌为负）、成交量（当日累计成交量，股，只能不低于）。`WatchThesis.entry_conditions` 至少一条，任一满足就算满足；`InvestmentThesis.target_price` 是单独一个字段，`invalidation_conditions` 可以没有
- 股票池的记录 `PoolEntry` 带着论点：关注有 `watch_thesis`，持仓有 `investment_thesis`，候选都没有（stock-pool 的不变量）。事件检测每一轮开始时已经读一次 `pool.entries()`
- 事件检测（`workflows/event_detection.py`）：守护线程，每轮做完等 60 秒（`self._stopping.wait(seconds)`）；第 2 部分在常规时段读一次账户、一次取齐行情，每只股票判断价格和成交量；`_record` 记下、写日志、排队
- 事件的类别已经有 `ENTRY`、`THESIS_CONDITION`，调度器要求的子 agent 都是 Quant；`define_case` 按持仓风险级别开持仓风险事件。替身对关注股的入场条件满足事件给出开仓方案，其余事件案件给出持有或继续关注
- 行情的 `Quote` 有最新价、前一交易日收盘价、量比、更新时刻，没有成交量；moomoo 适配器用快照，快照里有 `volume`（常规时段累计成交量，浮点数）
- OpenD 行情连接只有一个（`open_quote_context`），行情和寻股的股票列表共用；SDK 自己断线重连
- 迁移到 v0014

moomoo-api 10.11.7108 的到价提醒（读源码，查 openapi.moomoo.com 的文档，2026-11-02）：

- `set_price_reminder(code, op, key, reminder_type, reminder_freq, value, note, reminder_session_list)`：`op` 是 ADD、DEL、MODIFY、ENABLE、DISABLE、DEL_ALL；新增返回 key。每 30 秒最多 60 次；每只股票每种类型最多 10 个，文档没写全账户的上限
- `get_price_reminder(code=None, market=None)`：一只股票或一个市场的全部提醒，列 code、key、reminder_type、reminder_freq、value、enable、note、reminder_session_list
- 类型：PRICE_UP、PRICE_DOWN、CHANGE_RATE_UP、CHANGE_RATE_DOWN（当日涨跌幅，百分比：5 就是 5%）、VOLUME_UP（股）等；值精确到 3 位小数，多的舍去，美股成交量按 10 股向下取整
- 频率：ALWAYS、ONCE_A_DAY、ONCE；时段（只对美股）：OPEN、US_PRE、US_AFTER、US_OVERNIGHT，SDK 默认 OPEN、US_PRE、US_AFTER
- 备注：SDK 注释写最多 10 个字符，文档写 20 个中文字符
- 推送：`set_handler(PriceReminderHandlerBase 的子类)`，不用订阅；回调在 SDK 的回调线程里执行。handler 登记在连接对象上，断线重连以后照样有效（`_handler_ctx` 不随断线清掉）
- 文档没写：提醒是否同步到 moomoo app、触发时是否推送到手机；断线期间触发的提醒重连以后会不会补推
- 2026-11-02 在部署主机上实测：价格越过提醒的值时几秒内就有推送；设上时已经满足的提醒不推送（SPY 价格涨到现价的 99%，30 秒没有推送）。读到价提醒列表每 30 秒最多 10 次（超了返回 "Maximum 10 times per 30 seconds"）

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/marketdata/domain/quote.py   (改) Quote.volume: int | None
integrations/moomoo/
  quotes.py         (改) 映射 volume
  alerts.py         (新) MoomooPriceAlerts、ReminderContext、_PushHandler
  __init__.py       (改) 导出 MoomooPriceAlerts、ReminderContext
modules/events/
  domain/ids.py     (改) EventKind.ENTRY、TARGET、INVALIDATION
  domain/price.py   (改) Reading.volume
  domain/conditions.py (新) ConditionGroup、condition_groups、condition_event
  domain/alerts.py  (新) AlertKind、PriceAlert、AlertChanges、wanted_alerts
  ports.py          (改) PriceAlerts、PriceAlertsUnavailableError
  service.py        (改) AlertService
  __init__.py       (改) 导出上面新加的
workflows/event_detection.py (改) DetectionParts.alerts，论点条件、同步、叫醒
bootstrap/app.py    (改) QuoteContext 加 ReminderContext；装配 AlertService
```

- 跨模块传递的 Pydantic 模型：`contracts.thesis.Condition`、`WatchThesis`、`InvestmentThesis`（股票池的记录带着，workflow 交给事件模块的 domain），`contracts.event.Event`（不变）。其余是 frozen dataclass：行情的 `Quote`，事件模块的 `PriceAlert`、`AlertChanges`
- 事件模块的 domain 新 import 的只有 `contracts.thesis`；纯区域白名单不变
- integrations/moomoo 实现两个模块的端口：行情模块的 `QuoteSource`、事件模块的 `PriceAlerts`，都只 import 包根

## Goals / Non-Goals

**Goals:**

- 论点条件和价格异动走同一条路：同一次行情、同一个「行情要是当天的」、同一种事件 ID 去重，规则是 domain 的纯函数
- 到价提醒坏了（OpenD 没开、额度满、推送丢了）只让事件晚到一轮，不会漏掉
- 到价提醒的集合由股票池算出来，跟着每一轮同步，不用在股票池和下单的各处加钩子

**Non-Goals:**

- 不检测 OpenD 的断线和重连（D3）
- 不订阅实时报价推送
- 不给到价提醒写程序日志：它不是事实，只是叫醒

按「编程风格」省略的情况：

- 系统设的提醒被人在 app 里停用：仍算设着，不重新启用；推送没了，条件照样每轮判断
- 系统设的提醒被人在 app 里改了值：值不同，下一轮删掉重设
- 本机开发和主机连同一个 moomoo 账户：两边互删对方的提醒，只让推送失效
- 涨跌幅不低于 0 对应的"涨幅达到 0"：OpenD 拒绝时按一个提醒设置失败处理
- 一只股票同一种提醒超过 10 个：按设置失败处理
- 一轮之内论点或状态变了：按这一轮开始时读到的
- 提醒的值在 OpenD 的精度以下（50.1234 设成 50.123）：提醒早一点点触发，事件按原值判断
- 持仓今天开仓、涨跌幅条件仍从前一交易日收盘价算（Condition 的定义）
- 停牌、半天交易日：按交易日历和行情源给的

## Decisions

### D1 条件只由每一轮判断，推送只叫醒

可选的三种：

| 做法 | 问题 |
| --- | --- |
| 只靠推送，推送就是事件；断线恢复以后补查 | 主机常有几秒到几十秒的断网，断线期间触发的提醒要靠补查；补查要知道什么时候断过、哪些条件没推送过；推送的判断（moomoo 的盘前盘后、它的涨跌幅基准）和价格异动的规则不一样 |
| 推送和每一轮各自产生事件，事件 ID 去重 | 两份规则可能判断不一致：一路先到成了事件，另一路判断不出；测试要两套 |
| 每一轮判断是唯一的来源，推送只让下一轮马上开始 | 推送到事件之间多一次快照（几十毫秒） |

选第三种。事件只有一个来源，一份规则；推送丢了、到价提醒设不上、OpenD 断过，结果都一样：最多晚一个间隔（60 秒）。OpenD 只在价格越过提醒的值时推送，设上时已经满足的不推（Context 的实测），这种情况也一样由下一轮判断出来。Proposal 要的"断线恢复后用实时行情补查"就是恢复以后的那一轮，不用另写（D3）。简版写的是"价格类条件用 moomoo 到价提醒"，这里照样设提醒，它让入场和证伪的事件从最多晚 60 秒变成几秒。

完整版 Proposal 「到价提醒」和「到价提醒的维护」两句话照这个改了（proposal 的「需要人决定」(c)）。

考虑过：既然每轮都判断，干脆不设提醒。不符合简版，也少了几秒级的反应。

### D2 条件组和事件

```python
@dataclass(frozen=True, slots=True)
class ConditionGroup:
    """一只股票这一轮要判断的一组条件，任一满足就是这一组的事件。

    kind 是 EventKind.ENTRY、TARGET 或 INVALIDATION，决定事件 ID 的种类、类别和持仓风险级别。
    """

    kind: EventKind
    conditions: tuple[Condition, ...]


def condition_groups(
    watch: WatchThesis | None, investment: InvestmentThesis | None
) -> tuple[ConditionGroup, ...]: ...


def condition_event(
    symbol: str,
    group: ConditionGroup,
    reading: Reading,
    at: Detection,
    calendar: TradingCalendar,
) -> Event | None: ...
```

- `condition_groups`：关注论点给入场条件一组；投资论点给目标价一组（`Condition(kind=PRICE, comparison=AT_LEAST, value=target_price)`，正是 thesis.py 的注释说的"由代码换成条件"）和证伪条件一组（没有条件时不给）；候选两样都没有，给空
- `condition_event`：先过 `today_session`（和价格异动同一个「行情要是当天的」），再逐条比较；满足的条件写进说明
- 比较：价格比最新价；涨跌幅用乘法 `last >= previous_close * (1 + value)`，和价格异动一样不受 Decimal 舍入影响；成交量比 `Reading.volume`，没有时不满足
- 事件：

| 组 | 事件 ID 的种类 | 类别 | 持仓风险级别 |
| --- | --- | --- | --- |
| 入场条件 | entry | `EventCategory.ENTRY` | 否 |
| 目标价 | target | `EventCategory.THESIS_CONDITION` | 否 |
| 证伪条件 | invalidation | `EventCategory.THESIS_CONDITION` | 是 |

- 来源行情，强度和引用都没有，发生时刻是行情的更新时刻，处理一律排队
- 证伪条件触发是持仓风险级别：`define_case` 已经把持仓的持仓风险级别事件开成持仓风险事件（优先级 1）
- 目标价和证伪条件分开两种事件 ID：同一天先到目标价、又跌破证伪价位（比如涨跌幅类的证伪条件），两个都要开
- 证伪条件触发同时达到紧急阈值时也排队，不像价格下跌那样交给紧急风险退出：要判断这个得读账户数据的紧急阈值，而价格下跌事件已经记下了交给紧急退出的那一个；多出来的案件开始时这只股票已经是候选，按候选开（proposal 的「需要人决定」(a) 第 8 条）
- 事件格式不变：`Event.strength` 本来就可以没有，`EventSource.QUOTES` 本来就有

考虑过：

- 每个条件一个事件 ID：PM 写了三条入场条件，一天可能开三个案件；Proposal 说任一满足就算满足
- 只在越过的那一刻开：要记下上一轮满足没有，重启以后还要恢复；每天一个已经限住了数量

### D3 不检测断线

SDK 有两种断线：API 和 OpenD 之间（SDK 自己重连，`on_api_socket_reconnected`），OpenD 和 moomoo 服务器之间（系统通知 `SysNotifyType.CONN_STATUS`）。按 D1，两种都不用知道：

- 断线期间快照报错，第 2 部分按「读不到时」跳过，写 WARNING
- 恢复以后的第一轮按那时的行情判断，满足的照常成为事件
- handler 登记在连接对象上，重连以后不用重新登记
- 到价提醒存在 moomoo 服务器上，OpenD 重启以后还在；每一轮的同步也会补上

### D4 行情加成交量；条件只在常规时段判断

`Quote.volume: int | None`：快照的 `volume`（常规时段累计成交量）转成 int，和最新价、量比一样只在开盘 1 分钟以后给（moomoo-quotes spec「快照的映射」的表）。切换以前它是前一天的，切换以后、开盘以前也可能不为 0，所以不能不看最新价单独给。`Reading.volume` 跟着加。

条件只在「行情要是当天的」满足时判断，理由和价格异动相同：`last_price`、`volume` 本来就只算常规时段，系统也只在常规时段下单。成交量条件不等开盘 30 分钟：那是给量比的，累计成交量是绝对值，到了就是到了。

已有构造 `Quote` 的地方（行情的测试替身、moomoo 的测试、紧急退出和下单流程的测试）都要补上 `volume`；字段没有默认值。

### D5 需要的到价提醒

```python
class AlertKind(StrEnum):
    PRICE_UP = "price_up"  # 价格涨到
    PRICE_DOWN = "price_down"  # 价格跌到
    CHANGE_UP = "change_up"  # 当日涨幅达到，比例
    CHANGE_DOWN = "change_down"  # 当日跌幅达到，比例，正数
    VOLUME_UP = "volume_up"  # 当日成交量达到，股


@dataclass(frozen=True, slots=True)
class PriceAlert:
    symbol: str
    kind: AlertKind
    value: Decimal


def wanted_alerts(groups: Mapping[str, tuple[ConditionGroup, ...]]) -> frozenset[PriceAlert]: ...
```

- 按 price-alerts spec「需要的到价提醒」的表，一条条件对应一个或零个提醒；返回集合，一样的只留一个
- 和事件用同一份 `condition_groups`：目标价换成条件只写一次
- 涨跌幅存比例，换成 moomoo 的百分比在适配器里（D7）：domain 不知道行情源的单位

### D6 每一轮同步，只管系统设的

```python
@dataclass(frozen=True, slots=True)
class AlertChanges:
    added: int
    removed: int
    failed: tuple[tuple[PriceAlert, str], ...]  # 设不上或删不掉的提醒和行情源给的原因


class PriceAlertsUnavailableError(RuntimeError):
    """读不到系统设的到价提醒：这次没有同步。"""


class PriceAlerts(Protocol):
    def sync(self, wanted: frozenset[PriceAlert]) -> AlertChanges: ...

    def listen(self, on_push: Callable[[], None]) -> None: ...
```

- 什么时候同步：事件检测每一轮的最后（event-detection spec「一轮」第 6 部分），用这一轮开始时读到的股票池。论点更新、升降级、买入卖出（股票池跟上持仓）、移出都会改股票池的记录，下一轮就跟上；不用在股票池的用例里通知，也不用单独的线程。这一轮的条件已经按新的论点判断，提醒晚到不影响事件
- 同步放在最后：它不影响这一轮的事件，读不到时也不拖慢前面几部分
- 端口是"同步成这一组"，不是增删查三个方法：认出系统设的提醒、按 OpenD 的精度比较值都是 moomoo 的细节（D7），放在适配器里
- 认出系统设的：备注 `TAPro`，SDK 和文档的长度上限都够。账户只给系统用（交易），但人可能在 app 里设过提醒，那些不碰；它们的推送只多叫醒一轮
- `AlertService(alerts)` 在 `service.py`：`sync(groups) -> AlertChanges` 算出需要的再交给端口；`listen(on_push)` 原样交给端口。读不到时 `PriceAlertsUnavailableError` 原样抛出，由 workflow 写 WARNING，和其他数据源一样
- workflow 的日志：有增删时 INFO（设了几个、删了几个），每个失败一条 WARNING；不写程序日志：提醒不是事实，程序日志的事件检测层一行只记事件
- 额度：每只股票每种类型 10 个，关注最多 8 只、股票池最多 30 只，到不了；文档没写全账户的上限，真遇到时按一个提醒设置失败处理，下一轮再试，每分钟一条 WARNING

考虑过：

- 在股票池的每个改论点、改状态的用例里同步：股票池模块要依赖到价提醒，买入卖出还在巡检的股票池跟上持仓里；漏一处就不同步
- 只在启动时和论点变了时同步：要比较上一次，重启以后还要从头来；每轮一次读列表，一分钟一次请求

### D7 moomoo 适配器

```python
class ReminderContext(Protocol):
    """适配器用到的 OpenD 行情连接的那部分：moomoo 的 OpenQuoteContext 满足它。"""

    def get_price_reminder(
        self, code: str | None = None, market: str | None = None
    ) -> tuple[int, Any]: ...

    def set_price_reminder(
        self,
        code: str,
        op: str,
        key: int | None = None,
        reminder_type: str | None = None,
        reminder_freq: str | None = None,
        value: float | None = None,
        note: str | None = None,
        reminder_session_list: list[str] | None = None,
    ) -> tuple[int, Any]: ...

    def set_handler(self, handler: object) -> int: ...


class MoomooPriceAlerts:
    def __init__(self, context: ReminderContext) -> None: ...
```

- `sync`：`get_price_reminder(market=Market.US)`，不是 `RET_OK` 时抛 `PriceAlertsUnavailableError(f"OpenD 到价提醒：{data}")`；留下备注是 `TAPro` 的，每行换成 `(股票, 类型, 值)` 和 key；和需要的比较，多的按 key `DEL`，少的 `ADD`，先删后设。一次调用失败记进 `failed`，其余照做
- 类型和值（moomoo-price-alerts spec「提醒的设置」）：涨跌幅乘 100；价格和百分比 `quantize(Decimal("0.001"), ROUND_DOWN)`，成交量 `value // 10 * 10`；读回的值 `Decimal(repr(float(v)))` 再按同样的规则取整，再比较。这样下一轮不会因为 50.1234 被 OpenD 存成 50.123 而删掉重设
- 频率 `ONCE_A_DAY`：推送只用来叫醒，每天一次足够，app 也少推几次；`ALWAYS` 在价格来回穿越时会一直推。时段只给 `OPEN`：盘前盘后不判断，推了也是白叫醒
- 不用 `MODIFY`：值变了就是删一个设一个，结果一样，少一种路径
- 失败只看返回值：SDK 把超时、断线、限频、参数错误都放在返回值里，不抛异常（和行情适配器一样）
- mypy：SDK 是 Any，`class _PushHandler(PriceReminderHandlerBase)` 在 strict 下报 subclassing Any，这一行加 `# type: ignore[misc]`

### D8 推送和叫醒

```python
class _PushHandler(PriceReminderHandlerBase):  # type: ignore[misc]
    def __init__(self, on_push: Callable[[], None]) -> None:
        super().__init__()
        self._on_push = on_push

    def on_recv_rsp(self, rsp_pb: object) -> tuple[int, None]:
        self._on_push()
        return RET_OK, None
```

- `listen` 调一次 `set_handler(_PushHandler(on_push))`。不解析推送内容：推的是哪个提醒不影响接下来做什么（D1）
- 回调在 SDK 的回调线程里，只调 `EventDetector.wake`，它只设一个 `threading.Event`：不读写数据库、不拿锁、不阻塞 SDK 的线程
- `EventDetector`：

```python
class EventDetector:
    def wake(self) -> None:
        """叫醒事件检测：在等的立即做下一轮，在做的做完以后再做一轮。"""
        self._woken.set()

    def _loop(self) -> None:
        seconds = self._config.interval.total_seconds()
        while not self._stopping.is_set():
            self._woken.clear()
            started = self._parts.clock.now()
            try:
                self.run_round()
            except Exception:
                _log.exception("事件检测：这一轮出错")
            if self._woken.wait(seconds) and not self._stopping.is_set():
                gap = self._config.min_gap - (self._parts.clock.now() - started)
                self._stopping.wait(max(gap.total_seconds(), 0))
```

- 一轮开始时清掉叫醒，这一轮里再来的推送让 `wait` 立即返回，只多做一轮；`stop` 先设 `_stopping` 再设 `_woken`，在等的立即结束，停止以后的推送不再开始一轮
- 最短间隔（`EventDetectionConfig.min_gap`，默认 5 秒）：被推送叫醒以后，离上一轮开始不到最短间隔就再等到那一刻，等的时候来的推送留在 `_woken` 上，下一轮开始时一起清掉。开盘时一批提醒同时越过，每 5 秒最多一轮：读到价提醒列表每 30 秒最多 6 次（OpenD 限 10 次），快照每 30 秒多 6 次。按时钟算已经过了多久，测试的 `FakeClock` 不走，等的就是整个最短间隔
- `start` 先 `alerts.listen(self.wake)` 再开线程
- 叫醒的这一轮就是平常的一轮：也取新闻、读财报日历、同步提醒。推送一天每个提醒最多一次，多出来的请求有限；公告仍按公告间隔

考虑过：叫醒以后只判断论点条件。要再拆一种部分轮，规则和顺序多一份；收益只是少几次请求。

### D9 装配；没有迁移

- `bootstrap/app.py`：`QuoteContext` 的基类加上 `ReminderContext`；`AlertService(MoomooPriceAlerts(quotes))`，和行情、股票列表用同一个 OpenD 行情连接；`DetectionParts(alerts=...)`
- `tests/bootstrap/externals.py` 的替身 OpenD 跟着实现 `get_price_reminder`、`set_price_reminder`、`set_handler`
- 事件照样存在 `events_detected`（JSONB），新的事件 ID 种类不用改表；到价提醒存在 moomoo 服务器上，状态库不存。所以没有迁移，最新版本仍是 v0014

### D10 测试

- `tests/modules/events/domain/test_conditions.py`：event-rules spec「论点条件」的每个场景，以及「事件的内容」「事件 ID 和去重」新加的场景；比较的两侧从条件的值算出来
- `tests/modules/events/domain/test_alerts.py`：price-alerts spec「需要的到价提醒」的场景
- `tests/fakes/alerts.py`：`FakePriceAlerts`，内存里的一组提醒；记录每次 `sync` 收到的、可以安排下一次读不到或某个提醒失败；`push()` 调登记的回调
- `tests/modules/events/test_service.py` 加 `AlertService`
- `tests/integrations/moomoo/test_alerts.py`：`FakeOpenD` 满足 `ReminderContext`，返回 DataFrame（列和 SDK 一样，值是 numpy 的 float64），记录收到的调用；moomoo-price-alerts spec 的全部场景；推送用 `_PushHandler.on_recv_rsp` 直接调
- `tests/integrations/moomoo/test_quotes.py`、`tests/modules/marketdata/`：成交量
- `tests/workflows/test_event_detection.py`：event-detection spec 改过和新加的场景，price-alerts spec「同步」「同步失败」「推送」的场景；叫醒照 `test_queue_runner.py` 的线程写法，用 `FakeClock` 和线程事件，不计时
- 冒烟测试 `tests/integrations/moomoo/test_opend_alerts_smoke.py`（`moomoo_opend` 标记，在部署主机上对主机的 OpenD 跑，部署本变更以前跑）：对 SPY 设一个远高于现价的系统提醒，读回来值、频率、时段、备注都对，删掉；常规时段内在现价上下各 3 分设一个价格涨到和价格跌到，120 秒内收到推送（OpenD 只在越过时推送；收不到就停下报告给人）；跑的时候看一眼 moomoo app 有没有显示、手机有没有收到推送
- 主机上的程序和冒烟测试共用一个 moomoo 账户：账户上已经有备注 TAPro 的提醒时冒烟测试跳过，免得同步删掉程序的提醒；跑完以后不论通过没有，都按 key 删掉这次设上的提醒；整个文件只读 7 次列表，结束时被限频就等 31 秒再读一次
- `tests/bootstrap/`：bootstrap spec 新加的场景

## Risks / Trade-offs

- [设上时已经满足的提醒不推送（实测），断线期间触发的会不会补推没有验证] → 按 D1 推送只是提前，这两种都只是晚到一轮
- [一批提醒同时触发，叫醒的轮次超过 OpenD 读列表的频率限制] → 最短间隔 5 秒（D8）；真被限频时按读不到处理，下一轮再同步
- [moomoo app 可能也推送这些提醒到手机] → 跑冒烟测试时看一眼；嫌吵可以在 app 里关掉到价提醒的通知，不影响 OpenD 的推送（这一点也要在冒烟测试时核对）
- [`get_price_reminder` 的频率限制文档没写] → 每分钟一次，和设置的 60 次每 30 秒相比很少；被限频时按读不到处理，下一轮再来
- [条件一直满足时每天开一个案件，吃预算] → 每只股票每天每组最多一个；PM 改论点、降级或移出就停；替身的入场条件几乎总是满足，模拟盘上每只关注股每个交易日最多一个入场案件
- [moomoo 的涨跌幅提醒的基准可能和我们的不同（比如盘前算进去）] → 只影响叫醒的时机，事件按我们的规则判断

## Migration Plan

- 没有迁移；部署照常
- 部署以后的第一轮给股票池里全部关注股和持仓设提醒；入场条件已经满足的关注股在第一个常规时段的第一轮开出入场案件
- 运维：盯几天 Python 日志里的到价提醒同步和 WARNING，看程序日志里入场、目标价、证伪的检测到事件
- 回滚只 revert 代码；moomoo 上留下的系统提醒在 app 里删掉，或者回滚前把股票池清空跑一轮（不必要：留着只是多几条推送）
