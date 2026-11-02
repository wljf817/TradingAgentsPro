# Tasks

## 1. 前提

- [x] 1.1 按 proposal「需要人决定」(c) 修改完整版 Proposal 的三句话（propose 时已改）。验证：`grep -n "收到推送立即用实时行情判断\|推送只让它提前判断\|论点里的条件只在常规交易时段判断" "docs/AI Agent 美股交易系统需求 Proposal.md"` 找到改过的三处；简版不改

## 2. 行情的成交量

- [x] 2.1 先写测试：`tests/modules/marketdata/test_service.py` 照 market-data spec 改过的「取行情」（成交量、没有最新价时没有成交量）；`tests/integrations/moomoo/test_quotes.py` 照 moomoo-quotes spec 改过的「快照的映射」（夏令时的快照、还没切换、切换以后、开盘 1 分钟以后的成交量）；`tests/fakes/quotes.py` 和其他构造 `Quote` 的测试补上 `volume`。验证：确认因为 `Quote.volume` 不存在而失败
- [x] 2.2 实现（design D4）：`modules/marketdata/domain/quote.py` 的 `Quote.volume: int | None`，`integrations/moomoo/quotes.py` 映射快照的 `volume`（和最新价同一个条件）；`src/` 里构造 `Quote` 的地方跟着改。验证：2.1 的测试和原有的行情、紧急退出、下单流程、事件检测测试通过；`uv run mypy` 通过

## 3. 论点条件的规则

- [ ] 3.1 先写测试 `tests/modules/events/domain/test_conditions.py`（design D2）：event-rules spec「论点条件」的全部场景，「事件的内容」的入场条件满足事件的内容，「事件 ID 和去重」的条件一直满足时每天一个、价格下跌不挡证伪条件；比较的两侧从条件的值算出来（比如入场价、入场价加 0.01）；`condition_groups` 对候选、关注、持仓（有和没有证伪条件）给出的组。验证：确认因为 `condition_groups`、`condition_event`、`EventKind.ENTRY` 不存在而失败
- [ ] 3.2 实现（design D2、D4）：`modules/events/domain/ids.py` 的 `EventKind.ENTRY`、`TARGET`、`INVALIDATION`；`domain/price.py` 的 `Reading.volume`；`domain/conditions.py` 的 `ConditionGroup`、`condition_groups`、`condition_event`（复用 `today_session`，说明用 `text.py` 的写法）；`__init__.py` 导出，模块说明加上论点条件。验证：3.1 的测试和原有的 `tests/modules/events` 通过；`uv run pytest tests/architecture`、`uv run mypy`、`uv run lint-imports` 通过，纯区域白名单没有改

## 4. 需要的到价提醒和端口

- [ ] 4.1 先写测试：`tests/modules/events/domain/test_alerts.py` 覆盖 price-alerts spec「需要的到价提醒」的四个场景；`tests/fakes/alerts.py`（`FakePriceAlerts`：内存里的一组提醒，记录每次 `sync` 收到的，能安排下一次读不到、某个提醒失败，`push()` 调登记的回调）；`tests/modules/events/test_service.py` 加 `AlertService` 的 `sync`（算出需要的再交给端口、读不到原样抛出）和 `listen`。验证：确认因为 `wanted_alerts`、`AlertService` 不存在而失败
- [ ] 4.2 实现（design D5、D6）：`domain/alerts.py` 的 `AlertKind`、`PriceAlert`、`AlertChanges`、`wanted_alerts`；`ports.py` 的 `PriceAlerts`、`PriceAlertsUnavailableError`；`service.py` 的 `AlertService`；`__init__.py` 导出。验证：4.1 的测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 5. moomoo 到价提醒

- [ ] 5.1 先写测试 `tests/integrations/moomoo/test_alerts.py`（design D7、D8）：`FakeOpenD` 满足 `ReminderContext`，`get_price_reminder` 返回 DataFrame（列和 SDK 的一样，值是 numpy 的 float64），记录 `set_price_reminder` 和 `set_handler` 的调用，能安排返回错误；覆盖 moomoo-price-alerts spec 的全部场景；`alerts: PriceAlerts = MoomooPriceAlerts(...)` 让 mypy 检查满足端口。验证：确认因为 `integrations/moomoo/alerts.py` 不存在而失败
- [ ] 5.2 实现 `integrations/moomoo/alerts.py`：`ReminderContext`、`MoomooPriceAlerts`（读系统设的、取整比较、先删后设、失败记进结果）、`_PushHandler`；`__init__.py` 导出，模块说明加上到价提醒。验证：5.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过
- [ ] 5.3 冒烟测试 `tests/integrations/moomoo/test_opend_alerts_smoke.py`，加 `moomoo_opend` 标记，本机 127.0.0.1:11111 连不上时跳过（design D10）：任何时候对 SPY 设一个远高于现价的系统提醒，读回来类型、值、频率、时段、备注都对，再删掉、读不到；常规时段内设一个 SPY 价格涨到现价以下的提醒，30 秒内收到推送，结束时删掉。验证：OpenD 没开时跳过；写好以后告诉人去跑，并请人看 moomoo app 有没有显示这个提醒、手机有没有收到推送；推送收不到或字段不对就停下，报告给人

## 6. 事件检测接上

- [ ] 6.1 先写测试 `tests/workflows/test_event_detection.py`（design D1、D3、D6、D8）：event-detection spec 改过的「一轮」（同一只股票的价格事件和论点条件事件、入场条件满足排进案件、盘前照常同步、股票池是空的时删掉提醒）「没成为事件的新闻和公告」（论点条件没满足不写）「什么时候做一轮」（推送叫醒、做一轮时收到推送、停止以后的推送）「读不到时」（到价提醒不可用不影响事件）和新加的「断线以后」；price-alerts spec「同步」「同步失败」「推送」的场景；已有场景的股票池补上 event-detection spec 写的默认论点。验证：确认失败
- [ ] 6.2 实现 `workflows/event_detection.py`：`DetectionParts.alerts`；第 2 部分按 `condition_groups` 判断论点条件；第 6 部分同步到价提醒，读不到和失败写 WARNING，有增删写 INFO；`wake`、`_woken`、`start` 里先登记推送；模块说明写上推送在哪个线程、为什么只叫醒。验证：6.1 的测试和原有的事件检测测试通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 7. 装配

- [ ] 7.1 先写测试 `tests/bootstrap/`：bootstrap spec 改过的「模拟盘的装配」的新场景（事件检测设到价提醒、按论点条件排案件、替身给出开仓方案）；`tests/bootstrap/externals.py` 的替身 OpenD 实现 `get_price_reminder`、`set_price_reminder`、`set_handler`。验证：确认失败
- [ ] 7.2 实现（design D9）：`bootstrap/app.py` 的 `QuoteContext` 加 `ReminderContext`，装配 `AlertService(MoomooPriceAlerts(quotes))` 交给 `DetectionParts`。验证：7.1 的测试和原有的 `tests/bootstrap` 测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 8. 收尾

- [ ] 8.1 对照本变更的全部 spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 8.2 运行全部检查：`uv run pytest`（要开着 OrbStack）、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-thesis-conditions-and-price-alerts --strict`。验证：全部通过
