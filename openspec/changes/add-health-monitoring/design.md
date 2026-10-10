# Design

## Context

现状：

- `HealthService`（modules/health）：`check(signals)` 用时钟的现在、交易日历和配置判定一次，返回之后的状态；`reconciled()` 只在等待对账时回到正常；`status` 是属性，读内存。类的说明写明同一个实例一次只能有一个调用，由调用方保证。状态是 frozen dataclass `HealthStatus`（`mode`、`abnormal`、`degraded_since`、`degraded`），信号是 `HealthSignals`
- `bootstrap/startup.py` 的 `judge_health_at_startup` 启动时判定一次，只写日志；模型、预算、心跳按正常，券商按连接，行情取 `market_data.last_update()`，取不到为 None。模块说明写着告警由「健康监控接线」补上
- 盘中巡检（`workflows/patrol.py`，已归档）：守护线程每 15 秒一轮，`run_round` 依次跟进订单、同步预留、对齐止损单；第 3 步开始时 `execution.symbols_to_protect()` 读一次券商的全部持仓，`BrokerUnavailableError` 写 WARNING 并跳过这一步，别的异常由 `_item` 写 ERROR；每一项开始前看 `_stopping`。`TradingApp.lock` 是全程序一把 `RLock`
- 收盘对账（add-close-reconciliation，正在 apply）在 `run_round` 最后加「收盘对账」一项；新增 `workflows/reconciliation.py`，其中 `close_text` 和 `startup_text` 共用一个给出三类不一致各行的函数；`start` 在 Discord 连上以后、DBOS 以前推送启动对账的告警
- 下单流程（add-order-flow，正在 apply）：风控检查和再校验时读 `health.status.degraded`，不拿锁
- `MarketDataService.last_update()`：每次向 OpenD 取一次参考股票（SPY）的快照，失败抛 `QuoteUnavailableError`；OpenD 没连上时等连接超时（默认 10 秒）
- `ExecutionService.reconcile()`：第 1 步读券商全部挂单、止损单和持仓，超时或断线抛 `BrokerUnavailableError`、什么都不改；之后的跟进和对齐里的超时记为状态未知，对账照样完成
- Alpaca 改单时用新订单替换旧单，替换完成以前不接受第二次改单；几毫秒内对同一只股票对齐两次，第二次改单会被拒，这只股票被误标为无保护仓位（add-session-patrol 的 design D4）

要求见 specs/health-monitoring/spec.md 和 specs/bootstrap/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
workflows/
  health.py   (新) HealthMonitor（signals、judge）、degraded_text、recovered_text、checks_text
  patrol.py   (改) 构造参数加 health: HealthMonitor；_align_stops 给出读全部持仓的结果；
                   run_round 在第 3 步之后加「健康判定」一项
bootstrap/
  startup.py  (改) judge_health_at_startup 改用 HealthMonitor.signals；日志用检查的中文名
  app.py      (改) 装配 HealthMonitor，交给巡检；Discord 连上以后按第一次健康判定的结果推送告警
```

- `workflows/health.py` 只 import 模块的包根：`health`（`HealthService`、`HealthSignals`、`HealthStatus`、`HealthMode`、`HealthCheck`）、`execution`（`ExecutionService`、`BrokerUnavailableError`、`Reconciliation`）、`marketdata`（`MarketDataService`、`QuoteUnavailableError`）、`notification`（`NotificationService`）；以及 `shared.clock` 和收盘对账的 `workflows/reconciliation.py`（三类不一致的行）。不 import integrations、bootstrap
- 不新增跨模块的 Pydantic 模型，contracts 不改。模块之间传的是健康监控导出的 frozen dataclass `HealthSignals`、`HealthStatus` 和执行模块的 `Reconciliation`
- 任何模块都不改，也不加方法：券商连接取巡检已有的读取（D2），行情用已有的 `last_update`，对账用已有的 `reconcile`
- 没有新的配置项：用到的阈值是健康配置（数据延迟上限 60 秒、调度器心跳超时 300 秒）和巡检间隔（15 秒），都已经可配置

## Goals / Non-Goals

**Goals:**

- 健康判定是巡检的一项，单元测试直接调用 `HealthMonitor.judge` 或 `run_round`，不用线程、不用等时间
- 不改变任何模块的行为；健康状态仍只在内存里，degraded-mode spec 的状态机不改

**Non-Goals:**

按「编程风格」省略的情况：

- 一次读持仓失败就算券商断开，不防抖：偶发一次超时带来至少两轮的降级和进出两条告警
- 抖动时每次进入、离开降级模式各推送一条：不合并、不限频
- 降级期间异常的检查变了、降级和等待对账之间来回，只写日志不告警
- 巡检自己卡住时没有人判定，发现不了：没有看门狗；每一项的券商、OpenD、Discord 调用都有超时
- 读全部持仓出了超时、断线以外的错（401、数据库出错）时这一轮不判定，健康状态不变
- 恢复后的对账一直出错（数据库出错、401）时留在等待对账，每一轮写 ERROR，不告警
- 恢复后的对账在这一轮的对齐之后几毫秒，这一轮刚改过单的股票会碰上 Alpaca 不接受连续改单：只在上一轮到这一轮之间又有变化时出现
- 降级期间重启、重启以后已经正常的：没有降级模式结束的告警（启动日志写着健康正常）
- 盘外也取参考股票的行情：OpenD 没开时这一轮多等 10 秒；每一轮写一条取不到行情的 WARNING，不合并
- 降级模式结束的告警写已恢复开仓，不看单日亏损熔断：熔断有自己的告警
- 告警发不出去时不重发
- 记下的「最近一次取到的更新时刻」不持久化：重启以后从没有开始

## Decisions

### D1 在巡检每一轮的第 3 步之后判定

```python
def run_round(self) -> None:
    """做一轮：跟进订单、同步预留、对齐止损单、健康判定，最后是收盘对账。"""
    tracked = self._track_orders()
    self._sync_reservations()
    broker_connected = self._align_stops(tracked)
    self._judge_health(broker_connected)
    self._close_reconcile()
```

- 放在巡检里：巡检已经全天每 15 秒运行一次，判定、恢复后的对账都和巡检在同一个线程里，健康监控的调用自然串行（`HealthService` 要求调用方保证），对账也不会和巡检的各项同时进行
- 第 3 步之后：券商连接取第 3 步的读取（D2）；恢复以后，这一轮的跟进和对齐已经补上断线期间的变化，健康判定看到的是补完以后的情况
- 收盘对账保持一轮的最后一项（add-close-reconciliation 的 design D2）
- 「紧急退出流程」也往每一轮里加项：它的项和健康判定互不依赖（紧急退出在降级模式下照常有效），先后在归档时按那时的 `run_round` 排，只要收盘对账在最后
- `_judge_health` 和别的项一样：先看 `_stopping`；第 3 步没有读到结果（`None`）时不判定；包在 `_item("健康判定")` 里

考虑过：

- 单独一个线程定时判定：多一个线程的开始和停止；对账还要和巡检协调；判定也同样发现不了自己卡住
- 放在每一轮的最前面：券商连接要用上一轮的读取，晚 15 秒；恢复后的对账照样紧挨着这一轮的对齐，见 D5

### D2 券商连接取第 3 步读全部持仓的结果

```python
def _align_stops(self, tracked: set[str]) -> bool | None:
    """第 3 步，见 session-patrol spec「每一轮」。返回读券商全部持仓的结果，给健康判定用：

    读到为 True；超时或断线为 False；停止了、或者出了别的错没有读到为 None。
    """
```

- 巡检每一轮本来就读一次全部持仓：不多调用券商，session-patrol spec「没有持仓也没有订单」里「券商只收到一次读取全部持仓的请求」仍然成立；Alpaca 每分钟的请求不变（每分钟 200 次的限制，巡检最多约 92 次）
- 券商断开时只等一次超时（10 秒）：另做一次读取的话，断线的每一轮多等 10 秒
- 只看这一次读取：跟进和对齐里的单次超时已经按状态未知处理、下一轮再查，不算断开；第 3 步的读取每一轮都有，正好是每轮一次的探测
- 别的错（401、读状态库出错）不知道券商是否连得上：这一轮不判定，状态不变。巡检的 `_item` 已经写了 ERROR
- 启动时不读：启动对账刚读过券商，读取失败时已经启动失败（bootstrap spec，不变）

考虑过：

- 另做一次轻量的读取（比如读账户）：每一轮多一次请求，断线时多等 10 秒，session-patrol 的那个场景不再成立，还要给执行模块加一个只读方法
- 把巡检各项里的券商失败都算进来：一项超时就降级，太敏感；还要从执行模块里拿到被它吞掉的超时

### D3 行情：取不到时用上一次取到的时刻

```python
def signals(self, broker_connected: bool) -> HealthSignals:
    """一次判定的健康信号，见 health-monitoring spec「健康信号」。取不到行情时写 WARNING。"""
    try:
        self._last_quote_at = self._market_data.last_update()
    except QuoteUnavailableError as error:
        _log.warning("取不到行情（%s），行情最近一次更新的时刻按 %s", error, self._last_quote_at)
    return HealthSignals(
        pm_model_available=True,
        critic_model_available=True,
        broker_connected=broker_connected,
        last_quote_at=self._last_quote_at,
        reserve_budget_exhausted=False,
        scheduler_heartbeat_at=self._clock.now(),
    )
```

- `HealthMonitor` 在内存里记下程序启动以后最近一次取到的参考股票更新时刻。取不到时沿用它，所以 OpenD 一次超时、一次限流不会马上降级，仍然按数据延迟上限（60 秒）判断；一直取不到，满 60 秒就降级
- 还没取到过时为 None，健康监控从当天开盘起算（degraded-mode spec）：启动时 OpenD 没开，盘中就是降级，和现在一样
- 记下的是昨天的时刻也没关系：健康监控取它和今天开盘两者中较晚的那个
- 启动时的判定也用 `signals`，取到的时刻记下来给巡检接着用（D8）
- 盘外照样取：不用在这里判断时段；盘外的结果健康监控不看

考虑过：取不到一律为 None。盘中取不到时从开盘起算，开盘一小时以后，OpenD 任何一次失败都立即降级、两轮以后再恢复，带两条告警

### D4 调度器心跳按判定的时刻，模型和预算按正常

- 调度器是 M4 的案件循环（Proposal「调度器与运行模块」），M2 没有；按判定的时刻传入，这一项永远正常，和启动时（bootstrap spec）一样
- 考虑过用巡检上一轮做完的时刻当心跳：判定就在巡检的一轮里，巡检卡住时没有判定，发现不了它自己卡住；能发现的只是一轮做得久（超过 300 秒），而一轮做得久是因为券商或 OpenD 慢，它们有自己的检查。这样只会把券商慢误报成调度器异常
- 模型 API、调用预算等 M4、M5 的 Agent 网关；不为它们预留接口，到时改 `signals`

### D5 恢复后的对账等一轮

```python
def judge(self, broker_connected: bool) -> None:
    """巡检每一轮的健康判定：判定，按状态变化写日志和告警，等待对账满一轮时对账。"""
    before = self._health.status
    after = self._health.check(self.signals(broker_connected))
    self._report(before, after)
    if before.mode is after.mode is HealthMode.AWAITING_RECONCILIATION:
        self._recover(after)


def _recover(self, status: HealthStatus) -> None:
    """拿着锁对账、告诉健康监控对账完成；放开锁以后推送降级模式结束。"""
    try:
        with self._lock:
            result = self._execution.reconcile()
            self._health.reconciled()
    except BrokerUnavailableError as error:
        _log.warning("恢复后的对账没做成：券商不可用（%s），下一轮再来", error)
        return
    text = recovered_text(status, result)
    if result.clean:
        _log.info("%s", text)
    else:
        _log.warning("%s", text)
    self._notification.alert(text)
```

（示意：`judge`、`_recover` 是 `HealthMonitor` 的方法。）

- 各项恢复的那一轮（降级 → 等待对账）不对账：券商刚恢复的这一轮，第 1 步跟进补上断线期间的成交、第 3 步重挂被撤的止损单，都可能刚改过单；几毫秒以后对账再对齐同一只股票，Alpaca 还在替换，第二次改单被拒，误标为无保护仓位还会告警。断线恢复的第一轮正是最容易有变化的时候
- 下一轮判定仍然没有异常时对账：上一轮已经把变化都处理完，对账通常什么都不用改；对账以前同一轮的跟进和对齐也只处理这 15 秒里的新变化
- 顺带防了恢复时的抖动：要连续两次判定都没有异常才回到正常
- 对账和 `reconciled()` 在同一次拿锁里：对账以后、回到正常以前，别的调用方不会插进来；`reconciled()` 只改内存
- 判定（`check`）不拿锁：只读时钟和内存，风控和下单流程读 `status` 本来也不拿锁（add-order-flow）。判定在锁外，风控在进入降级的那一刻前后读到的可能是变化以前的状态，下一次检查再读
- 对账有不一致也调用 `reconciled()`：degraded-mode spec 已定对账完成就回到正常，不一致由执行模块自己暂停开仓
- `BrokerUnavailableError`（对账第 1 步）：什么都没改，留在等待对账；下一轮第 3 步读持仓失败就回到降级，成功就再对账。别的异常不接，交给巡检的 `_item` 写 ERROR，同样留在等待对账

考虑过：

- 恢复的那一轮立即对账：见上
- 恢复以后先等一个固定时长再对账：多一个配置项；一轮的间隔已经够替换完成

### D6 只在进入和离开降级模式时告警

```python
def _report(self, before: HealthStatus, after: HealthStatus) -> None:
    """按 health-monitoring spec「状态变化的告警和日志」的表写日志、推送告警。"""
    match before.mode, after.mode:
        case HealthMode.NORMAL, HealthMode.DEGRADED:
            text = degraded_text(after, at_startup=False)
            _log.warning("%s", text)
            self._notification.alert(text)
        case HealthMode.DEGRADED, HealthMode.DEGRADED if before.abnormal != after.abnormal:
            _log.warning("降级模式：现在异常的检查是%s", checks_text(after.abnormal))
        case HealthMode.DEGRADED, HealthMode.AWAITING_RECONCILIATION:
            _log.info("各项检查已恢复，下一轮和券商对账")
        case HealthMode.AWAITING_RECONCILIATION, HealthMode.DEGRADED:
            _log.warning("等待对账时又有检查异常：%s", checks_text(after.abnormal))
```

- 推送两种：进入降级模式（正常 → 降级）和降级模式结束（恢复后的对账完成，D5）。人关心的是开仓什么时候暂停、什么时候恢复
- 降级和等待对账之间来回不推送：两者都暂停开仓，对人没有区别；等待对账按 D5 通常只持续一轮（15 秒），单独推一条，15 秒后又是一条结束；数据延迟在上限附近抖动时，每 15 秒就是一条
- 降级期间异常的检查变了只写 WARNING：开仓已经暂停，人收到的进入告警已经让他去看了
- 日志写 Python 日志（M4 才有程序日志）；正常 → 正常不写，免得每 15 秒一条
- 推送在锁外：`_report` 在 `check` 之后、不在锁里；`_recover` 在放开锁以后推送

考虑过：三种状态变化都推送（进入降级、进入等待对账、回到正常）。抖动时刷屏，等待对账的那条和 15 秒后的结束重复

### D7 告警文字

```python
def checks_text(checks: Collection[HealthCheck]) -> str:
    """异常的检查的中文名，按 degraded-mode spec「健康检查」表的顺序，用顿号连接。"""
    return "、".join(_CHECK_NAMES[c] for c in HealthCheck if c in checks)


def degraded_text(status: HealthStatus, *, at_startup: bool) -> str:
    """进入降级模式的告警；at_startup 时是启动时处于降级模式。"""


def recovered_text(status: HealthStatus, result: Reconciliation) -> str:
    """降级模式结束的告警：status 是对账以前的状态，result 是这次对账的结果。"""
```

进入降级模式（启动时第一行是「启动时处于降级模式」）：

```
进入降级模式（2026-10-14 15:00 UTC 起），异常的检查：券商连接
已暂停开仓，已经确认、还没下单的开仓方案下单时作废；止损单照常保留。各项恢复并和券商对账以后恢复开仓
```

降级模式结束：

```
降级模式结束（2026-10-14 15:00 UTC 起）：各项检查已恢复，和券商对账没有不一致，已恢复开仓
```

对账有不一致时：

```
降级模式结束（2026-10-14 15:00 UTC 起）：各项检查已恢复，和券商对账有不一致：
- 来历不明的持仓：CCC 100 股
已暂停开仓，之后的对账没有不一致时恢复
```

- 中文名 `_CHECK_NAMES`：模型 API、券商连接、数据延迟、调用预算、调度器心跳，和 degraded-mode spec 的表一致；按 `HealthCheck` 的定义顺序（就是表的顺序）列出，不按字母排序
- 三类不一致的行和最后一行用收盘对账的写法：`workflows/reconciliation.py` 里 `close_text`、`startup_text` 共用的那个函数，启动对账、收盘对账、恢复对账的告警一样。它在收盘对账里如果是私有的，apply 时改为公开
- 时刻写 UTC、到分钟，和巡检的无保护告警、收盘对账一样；写进入降级模式的时刻（`degraded_since`）
- 启动日志里 `judge_health_at_startup` 的 WARNING 也改用 `checks_text`，不再写英文的枚举值

### D8 bootstrap 接线

- `_assemble`：建 `HealthMonitor(health, execution, market_data, notification, lock, clock)`，放进 `_Parts`，交给 `SessionPatrol(..., health=monitor)`
- `judge_health_at_startup(health, monitor)`：`health.check(monitor.signals(broker_connected=True))`，日志同现在；返回状态。取到的行情时刻由 `monitor` 记下，巡检接着用
- `start`：「启动 Discord」以后、「启动 DBOS」以前，在启动对账的告警（收盘对账加的）之后：

  ```python
  if health_status.degraded:
      parts.notification.alert(degraded_text(health_status, at_startup=True))
  ```

  不放进 `step(...)`：`alert` 不会抛错，告警失败不会变成启动失败
- 巡检第一轮判定时，之前的状态就是启动时判定的状态：仍是降级不再告警；恢复以后照 D5 对账、推送结束
- `startup.py` 的模块说明改为：启动对账和健康的告警由 `app.py` 在 Discord 连上以后发出
- bootstrap spec 只改「第一次健康判定」。「模拟盘的装配」的盘中巡检一行（调用执行模块、风控和通知）没有列出健康监控和行情：「下单流程」和「紧急退出流程」也在改这条要求，少一处冲突；巡检用到哪些部件由 health-monitoring spec 写明

### D9 测试

- `tests/workflows/test_health.py`：
  - 直接调用 `HealthMonitor.signals`、`judge`：信号表的每一行（模型、预算、心跳按正常，券商取参数，行情取到、取不到用上一次、还没取到过为 None、盘外取不到）、状态变化表的每一行（日志级别、有没有推送）、恢复后的对账表的每一行、对账读取失败、对账有不一致、Discord 报错、`degraded_text`、`recovered_text`、`checks_text` 的每一种写法
  - 用 `tests/workflows/builders.py` 的 `World`（加上 `FakeQuoteSource`、`HealthService`、`HealthMonitor`）调用 `run_round`，覆盖 spec 里经过巡检的场景：第 3 步读持仓超时算断开、跟进超时不算、401 时不判定、恢复的那一轮不对账下一轮对账、断线期间被撤的止损单在对账以前重挂、抖动、偶发一次超时、收盘以后、程序启动时处于降级模式（用 `judge_health_at_startup` 先判定一次）
  - 锁的场景：另一个线程拿锁时判定照样做完、对账等锁；通知替身阻塞时测试线程 `acquire(blocking=False)` 成功
  - 「券商断开」用 `FakeBroker.fail_next` 给读全部持仓、读挂单安排失败；401 和意外的错照 `test_patrol.py` 已有的写法替换替身的方法；SPY 的更新时刻在 `FakeQuoteSource` 上设
- `tests/workflows/test_patrol_thread.py`：停止时正在做恢复后的对账（读全部挂单阻塞到测试放行）
- `tests/workflows/test_patrol.py`：已有的场景不变；`World` 默认的 SPY 行情每次都是 10 秒前，健康判定不影响它们
- `tests/bootstrap/test_startup.py`：第一次健康判定的信号表和日志改用中文名；取到的行情时刻记在 `HealthMonitor` 里
- `tests/bootstrap/test_app.py`：bootstrap spec「第一次健康判定」的场景，检查 `FakeGateway` 收到的告警和它在「Discord 已连接」之后、「DBOS 已启动」之前；和启动对账告警的先后；巡检第一轮仍是降级时只有一条健康告警（有超时地等巡检做完第一轮）

## Risks / Trade-offs

- [券商偶发一次超时就降级，带进出两条告警，降级期间确认的开仓方案在再校验时作废] → 降级只持续两轮（约 30 秒）；Alpaca 在这段时间里本来也不可靠。实际太吵再加防抖
- [巡检卡住时健康监控发现不了] → 每一项的外部调用都有超时；真卡住时止损单也没人维护，靠 systemd 和人看日志；M4 的调度器心跳之后再看
- [每一轮多一次 OpenD 快照请求] → 每 30 秒 2 次，OpenD 的限制是 60 次；「紧急退出流程」取持仓行情的请求另算
- [恢复后的对账和同一轮的对齐挨着] → 等一轮再对账，见 D5；剩下的只有这 15 秒里又有变化的情况
- [对账在锁里，券商慢时拿锁几秒到几十秒] → 只在恢复时做一次；这时 `/plan` 的提交本来也在等降级结束

## Migration Plan

- 没有数据库迁移，没有新依赖
- 部署：更新代码、重启主程序；启动日志照旧写出健康正常；之后盘中断开 OpenD 一分钟以上，Discord 收到进入降级模式的告警，恢复以后约 30 秒收到降级模式结束（部署机器上的检查等「流程」组做完统一做）
- 回滚：revert 代码；健康状态只在内存里，没有要处理的数据
