# Tasks

## 1. 前提

- [x] 1.1 先做 add-moomoo-quotes 新加的量比任务（它的 tasks 6.1、design D9；本变更的 design D10）：按那一条先写测试、再实现 `Quote.volume_ratio` 和 moomoo 的映射，在真实 OpenD 上核对 `volume_ratio`。做完在 `openspec/changes/add-moomoo-quotes/tasks.md` 里勾掉 6.1。add-moomoo-quotes 不用先归档，开盘切换的修正也不挡本变更的 apply。验证：那一条写的验证全部通过；字段不存在或含义不同就停下，报告给人
- [x] 1.2 按 proposal「需要人决定」第 8 条修改完整版 Proposal 的三句话（2026-10-23 用户同意，propose 时已改）。验证：`grep -n "8-K 的 item 2.02\|每个交易日最多开一个案件\|价格和成交量异动只在常规交易时段判断" "docs/AI Agent 美股交易系统需求 Proposal.md"` 找到改过的三处；简版不改

## 2. 事件的格式和触发

- [x] 2.1 先写测试：`tests/contracts/test_event.py`（`Event` 原样序列化和读回、`extra` 报错、强度的 Decimal 不丢位）；`tests/modules/scheduler/domain/test_case_type.py` 加 case-types spec「触发」的「事件和卖出成交带的内容」；`tests/workflows/test_case_entries.py` 的触发原因带事件 ID 和客户端订单 ID（design D12）；`tests/modules/scheduler/test_service.py`、`test_repo.py`（OrbStack）和 `tests/workflows/test_queue_runner.py` 覆盖 queue-runner spec「排过的卖出成交」的三个场景，`tests/fakes/queue.py` 加 `sales`。验证：确认因为 `Event`、`EventTrigger.event_id`、`SellTrigger.order_id`、`sales` 不存在而失败
- [x] 2.2 实现（design D2、D4、D12）：`contracts/event.py` 的 `EventSource`、`EventOutcome`、`Event`，模块说明改为事件的格式在这里；`modules/scheduler/domain/case_type.py` 的 `EventTrigger.event_id`、`SellTrigger.order_id`；`ports.py` 的 `QueueStore.sales`、`repo.py` 读 JSONB 的 `order_id`、`service.py` 的 `QueueService.sales`；`workflows/queue_runner.py` 的 `QueueRunner.queued_sales`；`workflows/case_entries.py` 的触发原因。已有测试里构造这两种触发的地方补上新字段。验证：2.1 的测试通过；`uv run pytest tests/modules/scheduler tests/workflows tests/contracts`、`uv run mypy`、`uv run lint-imports` 通过

## 3. 事件规则

- [x] 3.1 先写测试 `tests/modules/events/domain/` 和 `tests/modules/events/test_config.py`（design D1、D3、D7 到 D11）：event-rules spec 每个 requirement 的全部场景，规则表每一行一个测试，阈值两侧的值从配置算出来、不在测试里写死（比如参考价乘以 1 加价格阈值）；关键词默认表的每一行；预计发布时刻的四种时段、发布日是周末；事件 ID 的交易日（盘前、盘中、收盘以后、休市日）；事件的说明写明 spec 要求的各项；配置的默认值和非法取值。验证：确认因为 `tradingagentspro.modules.events` 不存在而失败
- [x] 3.2 实现 `modules/events/config.py`（`EventRulesConfig`、`KeywordRule`、`FilingTiers`，默认的关键词表和公告分级）和 `domain/`（`price.py`、`news.py`、`filings.py`、`earnings.py`、`ids.py`、`text.py`），`__init__.py` 写模块说明并导出（design 的模块边界）。验证：3.1 的测试通过；`uv run pytest tests/architecture`、`uv run mypy`、`uv run lint-imports` 通过，纯区域白名单没有改

## 4. 事件的存储、用例和程序日志

- [x] 4.1 先写测试：`tests/fakes/events.py`（`InMemoryEventStore`：按事件 ID 去重、按股票和时刻读、可以安排下一次读写报错）；`tests/modules/events/test_service.py` 覆盖 event-detection spec「事件的存储」的同一个事件 ID 只写一次、「读取事件」的场景，以及 program-log spec「事件检测层的动作」里检测到事件和丢弃事件的条目内容（对象、引用、说明、没有案件 ID）；`tests/modules/events/test_repo.py`（要开着 OrbStack）覆盖原样读回（本机时区 UTC+14）、主键冲突、两个线程同时记下、读写出错、空数据库没有升级；`tests/bootstrap/test_database.py` 的表清单和最新迁移版本加上 `events_detected`、v0014。验证：确认因为 `EventService`、`PostgresEventStore` 不存在而失败
- [x] 4.2 实现（design D3、D14、D16）：`modules/events/ports.py`（`EventStore`）、`repo.py`（`events_detected` 表和 `PostgresEventStore`，`INSERT ... ON CONFLICT DO NOTHING RETURNING`）、`service.py`（`EventService.record(event, status) -> bool`、`discard(...)`、`event(event_id)`、`events(symbol, since)`）；`modules/programlog/domain/entry.py` 加 `Actor.EVENT_DETECTION`、`Action.DETECT_EVENT`、`Action.DISCARD_EVENT`；迁移 `v0014_create_events.py`。验证：4.1 的测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 5. 已经结束的卖出

- [x] 5.1 先写测试 `tests/modules/execution/test_trade_service.py`：trade-records spec「已经结束的卖出」的两个场景。验证：确认因为 `ExecutionService.sales` 不存在而失败
- [x] 5.2 实现（design D4）：`ExecutionService.sales(since: date)`，只读交易记录存储的 `traded()`。验证：5.1 的测试通过；`uv run pytest tests/modules/execution`、`uv run mypy` 通过

## 6. 快照和案件上下文带上事件

- [x] 6.1 先写测试：`tests/workflows/test_snapshot_flow.py` 照 state-snapshot spec 改过的「条目和条目 ID」「案件股票的事实」（事件的条目排在 surprise 之后、事件的回看起点和内容、没有事件）；`tests/contracts/test_snapshot.py` 的事件条目和条目 ID；`tests/workflows/test_case_flow.py` 照 case-workflow spec「案件上下文」（事件是整个事件、读不到事件时报错、跟进没有事件）；`tests/modules/gateway/test_agent_calls.py` 里 agent-calls spec「每个 agent 的输入和输出」的持仓的案件上下文；`tests/integrations/standin/test_backend.py` 里入场条件满足的事件仍然给出开仓方案。验证：确认因为 `EventEntry`、`Snapshot.events`、`CaseContext.event` 的新类型不存在而失败
- [x] 6.2 实现（design D13）：`contracts/snapshot.py` 的 `EntryKind.EVENT`、`EventEntry`、`Snapshot.events` 和 `entries()` 的先后；`contracts/agent_call.py` 的 `CaseContext.event: Event | None`；`workflows/snapshot_flow.py` 读这只股票的事件；`workflows/case_flow.py` 的 `CaseServices.events` 和 `context` 读事件；`integrations/standin/conclusions.py` 读 `context.event.category`。已有构造快照和案件上下文的测试补上事件。验证：6.1 的测试和原有的快照、案件工作流、替身、网关测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 7. 事件检测的运行

- [x] 7.1 先写测试 `tests/workflows/test_event_detection.py`（design D5、D6、D11、D12）：`run_round` 用 fakes 的股票池、执行模块的券商和交易记录、行情、新闻源和公告源（真的 `NewsService` 加内存存储）、公司数据、事件存储、程序日志和一个记下排队的 `CaseIntake` 替身，`FakeClock`；覆盖 event-detection spec 全部 requirement 的场景，以及 program-log spec「事件检测层的动作」的四个场景；`start`、`stop`、间隔和一轮出错照 `test_queue_runner.py` 的线程写法测「什么时候做一轮」；`EventDetectionConfig` 的默认值和非法取值。间隔、回看时长从配置读取。验证：确认因为 `workflows/event_detection.py` 不存在而失败
- [x] 7.2 实现 `workflows/event_detection.py`：`EventDetectionConfig`、`CaseIntake`、`EventSources`、`EventDetector`（`run_round` 的五部分、公告间隔、读不到时的处理和 WARNING、`start`、`stop`），模块说明写上它在哪个线程、读什么、为什么不拿锁。验证：7.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 8. 装配

- [ ] 8.1 先写测试 `tests/bootstrap/`：bootstrap spec 改过的「启动顺序」（日志里有事件检测已开始）、「模拟盘的装配」（排进的案件由替身跑到下单、新案件排队时作废等确认的方案、事件检测排进的案件跑起来、事件检测给止损单成交排卖出复盘）、「停止」（SIGTERM 的关闭顺序、停止时巡检正在跟进订单）；`tests/bootstrap/externals.py` 的行情和新闻替身跟着改。验证：确认失败
- [ ] 8.2 实现（design D15）：`bootstrap/app.py` 装配 `EventService`、`EventDetector`，快照流程和案件工作流拿 `EventService`；启动时排队运行之后开始事件检测，停止时在巡检之后关闭。验证：8.1 的测试和原有的 `tests/bootstrap` 测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 9. 收尾

- [ ] 9.1 对照本变更的全部 spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 9.2 运行全部检查：`uv run pytest`（要开着 OrbStack）、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-event-detection --strict`。验证：全部通过
