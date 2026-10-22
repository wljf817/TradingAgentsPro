# Tasks

## 1. 前提

- [x] 1.1 核对 DBOS 的用法（design D5、D6、风险的最后一条）：在 `tests/workflows/test_case_workflow.py` 的真的 DBOS 里（要开着 OrbStack）确认 `DBOS.list_workflows(name="case", status="PENDING")` 列出运行中的案件、不列子工作流 `case_task`、输入里能取到 `CaseDefinition`；`DBOS.recv` 带超时在重启以后只等剩下的时间。验证：一个临时的测试通过，结论写进 1.1 的完成说明；任何一项不对就停下，报告给人，不在本变更里绕过
  - 结论：`list_workflows(name="case", status="PENDING")` 只列出运行中的案件（子工作流的名字是 `case_task`），输入是 `{"args": (case_id, CaseDefinition, Trigger, triggered_at), "kwargs": {}}`；`recv` 的超时经 `record_sleep` 记下醒来的时刻，重放时读回它，只等剩下的时间（`_sys_db.py` 的 `record_sleep`）

## 2. 调度器的队列存储

- [x] 2.1 先写测试（design D2）：`tests/modules/scheduler/test_service.py` 用 `tests/fakes/queue.py` 的 `InMemoryQueueStore` 覆盖 queue-runner spec「排队」的分配 ID、先后、同样的触发排两次，「排队的案件的存储」的出队以后不在队列里、第二次出队报错；`tests/modules/scheduler/test_repo.py`（要开着 OrbStack）覆盖原样读回（四种触发、本机时区 UTC+14）、入队的先后、出队、读写出错、空数据库没有升级。验证：确认因为 `QueueService`、`PostgresQueueStore` 不存在而失败
- [x] 2.2 实现（design D2、D14）：`modules/scheduler/domain/queue.py` 的 `QueueExit`；`ports.py` 的 `QueueStore`；`repo.py` 的两张表、带种类的触发编码和 `PostgresQueueStore`；`service.py` 的 `QueueService`；`__init__.py` 导出、模块说明写上队列的存储；迁移 `v0013_create_scheduler_queue.py`；`tests/bootstrap/test_database.py` 的表的清单和最新迁移版本。验证：2.1 的测试通过；`uv run pytest tests/modules/scheduler tests/bootstrap/test_database.py`、`uv run mypy`、`uv run lint-imports` 通过

## 3. 下单流程收作废

- [ ] 3.1 先写测试（design D6）：`tests/workflows/test_order_flow.py` 覆盖 `settle` 的作废（报告已作废、释放的原因、不告警）和 `void_queued` 的两种结果（有订单交给巡检、没有订单释放并告警）及告警的文字；`tests/workflows/test_order_workflow.py`（真的 DBOS）覆盖 order-flow spec「作废案件的方案」的七个场景、「结果送回案件」的「被排队运行作废」、「告警」的「排队运行作废已经确认的方案的告警」，`FakeSleep` 改成满足 `wait` 的替身，能在指定的一次等待里交回作废。验证：确认因为 `Stage.VOIDED`、`void_plan`、`PlanEvent.VOIDED` 不存在而失败
- [ ] 3.2 实现（design D6）：`plan_handoff.py` 的 `PlanEvent.VOIDED`；`order_flow.py` 的 `Stage.VOIDED`、`settle` 的作废分支、`void_queued`；`order_workflow.py` 的 `_EVENTS`、`install(flow, wait)`、`_execute` 的等待收作废、step `order_plan.void_queued`、`void_plan`、`wait_decision`，模块说明写上作废；`bootstrap/app.py` 的 `Externals.sleep` 改名 `wait`、`SYSTEM_EXTERNALS` 用 `wait_decision`；`tests/bootstrap/externals.py` 的 `WorldSleep` 跟着改。验证：3.1 的测试通过；原有的 `tests/workflows`、`tests/bootstrap` 测试只改替身和参数名，全部通过；`uv run mypy`、`uv run lint-imports` 通过

## 4. 案件工作流：运行中的案件和作废

- [ ] 4.1 先写测试（design D5、D7）`tests/workflows/test_case_workflow.py`（真的 DBOS）：`running_cases` 给出运行中的案件的进展、还没发布进展的按输入给出刚开始的进展、结束和出错停下的不算；case-workflow spec「交给下单」的「方案被作废」（等人工确认、等下单）。验证：确认因为 `running_cases`、`_PLAN_EVENTS` 没有作废而失败
- [ ] 4.2 实现（design D5、D7）：`case_workflow.py` 的 `running_cases`、`_PLAN_EVENTS[PlanEvent.VOIDED]`，模块说明写上作废只在 `_deliver` 里收。验证：4.1 的测试通过，原有的案件工作流测试全部通过；`uv run mypy` 通过

## 5. 紧急退出流程给出正在紧急退出的股票

- [ ] 5.1 先写测试（design D9）`tests/workflows/test_emergency_rounds.py`、`test_emergency_confirmation.py`：`exiting` 在开始紧急退出、在等确认、确认以后、被拒绝和超时、清仓单结束、没有可卖的股数、到收盘还没有清仓单时的取值，重启以后按清仓单找回。验证：确认因为 `exiting` 不存在而失败
- [ ] 5.2 实现（design D9）：`emergency_flow.py` 的 `exiting` 和 `judge`、`advance`、`report` 末尾重算；模块说明写上。验证：5.1 的测试和原有的紧急退出测试通过；`uv run mypy` 通过

## 6. 排队运行

- [ ] 6.1 先写测试（design D3、D4、D10、D13）`tests/workflows/test_queue_runner.py`：`run_round` 用 `tests/fakes/cases.py` 的 `FakeCases`、内存的队列、股票池、案件记录、费用和程序日志，真的 `HealthService` 和 `FakeClock`，覆盖 queue-runner spec 的「一轮」「作废方案」「不再运行」「启动」「调度器心跳」「调用预算用完的日志」「重启以后」的场景，以及 program-log spec「调度器的动作」的排队、不再运行、方案作废和「系统的动作」的调用预算用完；`start`、`stop`、间隔和一轮出错照 `test_patrol_thread.py` 的写法测「什么时候做一轮」；`QueueRunnerConfig` 的不合法取值。并发上限、间隔从配置读取，不在测试里硬编码。验证：确认因为 `workflows/queue_runner.py` 不存在而失败
- [ ] 6.2 实现（design D3、D4、D10、D13）：`modules/programlog/domain/entry.py` 加四个 `Action`；`names.py` 加股票状态的中文名；`workflows/queue_runner.py` 的 `QueueRunnerConfig`、`CaseControl`、`QueueParts`、`QueueRunner`（`enqueue`、`run_round`、`start`、`stop`、`heartbeat`）、`DbosCases` 和程序日志的文字，模块说明写上。验证：6.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 7. 健康监控接线

- [ ] 7.1 先写测试（design D8）`tests/workflows/test_health.py`：health-monitoring spec「健康信号」改过和新加的场景（调度器心跳停了、PM 没有可用的模型、持仓保留预算用完、读预算时数据库出错、一轮做得久），模型的熔断用 `ModelFallbackService`，预算用内存的费用，心跳用给定的函数；`tests/workflows/test_patrol.py` 里构造 `HealthMonitor` 的地方跟着改。验证：确认因为 `HealthMonitor` 的新参数不存在而失败
- [ ] 7.2 实现（design D8）：`health.py` 的 `HealthMonitor` 加 `fallback`、`budget`、`heartbeat`，`signals` 取这三项；模块说明写上。验证：7.1 的测试和原有的健康、巡检测试通过；`uv run mypy` 通过

## 8. 装配

- [ ] 8.1 先写测试（design D12）`tests/bootstrap/`：bootstrap spec 改过的「环境变量」「启动顺序」「模拟盘的装配」「第一次健康判定」「DBOS」「停止」的场景，包括缺少两个新变量、启动时持仓保留预算用完、启动时恢复等确认的案件、替身跑到下单、新案件排队时作废等确认的方案；四个数据源用 `Externals.http` 的 `MockTransport` 按地址回答；`TradingApp.queue` 存在；`tests/deploy` 的模板有两个新变量。验证：确认失败
- [ ] 8.2 实现（design D12）：`settings.py` 的两个变量和 `Settings` 的两项；`bootstrap/app.py` 建网关、数据、快照、案件工作流、排队运行，`EmergencyFlow` 先建，一份 `StockPoolConfig` 和一份 `SchedulerConfig`，`QuoteContext` 加 `SecuritiesContext`，`Externals.http`，启动时依次 install 两个工作流、launch、开始排队运行、开始巡检，`on_stop` 的顺序，`TradingApp.queue`；`startup.py` 不变（`judge_health_at_startup` 照用 `monitor.signals`）；`deploy/tradingagentspro.env.example` 加两个变量和说明；模块说明写上。验证：8.1 的测试和 `tests/bootstrap`、`tests/deploy` 原有的测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 9. 收尾

- [ ] 9.1 对照本变更的八份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的要求里原有的场景仍由原来的测试覆盖。验证：对应表写在完成报告里
- [ ] 9.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
