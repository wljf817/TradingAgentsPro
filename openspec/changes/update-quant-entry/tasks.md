# Tasks

## 1. 合同和快照的行情

- [x] 1.1 测试：`contracts` 的量化指标条目和 ID、`TaskOutcome` 和 `SubAgentRequest` 带条目、失败的原因数据取不到（task-dispatch spec「Result 的格式」的「数据取不到的 Result」「模型和状态不符」）；state-snapshot spec「共享事实」的「开盘后还没有成交」「行情带量比和成交量」「读回还没有量比和成交量的快照」
- [x] 1.2 实现：`contracts/quant.py` 的 `QuantEntry`、`quant_entry_id`，`contracts/task.py` 的 `TaskFailure.DATA_UNAVAILABLE`，`contracts/agent_call.py` 的两个字段，`contracts/snapshot.py` 的 `QuoteEntry` 两项，`workflows/snapshot_flow.py` 抄上量比和成交量；用 `uv run pytest tests/contracts tests/workflows` 验证

## 2. 网关的存储

- [x] 2.1 测试：task-dispatch spec「Quant 任务的量化指标」的「原样读回」「不是 Quant 的任务没有条目」「读写出错」，内存和 Postgres 两种存储；state-store spec「表的归属和迁移」的新表
- [x] 2.2 实现：`ports.py` 的 `TaskStore.add_quant`、`TaskRecord.quant`；`tests/fakes/tasks.py`；`repo.py` 的 gateway_indicators，`record` 和 `case_records` 带上条目；迁移 v0019；用 `uv run pytest tests/modules/gateway tests/bootstrap` 验证

## 3. 运行 Quant 的任务

- [x] 3.1 测试：agent-calls spec「子 agent 的任务」的「Quant 的任务先取得量化指标条目」「取不到 K 线」「News 和 Fundamentals 的任务不取量化指标」「已经有 Result 的任务不再调用」；task-dispatch spec「Quant 任务的量化指标」的「只算一次」「K 线取不到」；signal-format spec「Quant 引用自己任务的量化指标条目」；用 `tests/fakes` 里新加的 `QuantNumbers` 替身
- [x] 3.2 实现：`ports.py` 的 `QuantNumbers`、`QuantDataUnavailableError`；`domain/result.py` 的数据取不到；`TaskDispatchService.add_quant`、`unavailable`；`AgentCallService.run_task` 按 design D2；用 `uv run pytest tests/modules/gateway` 验证
- [x] 3.3 测试：`indicator_request` 覆盖「按 Task 和快照组出请求」「寻股的股票取粗筛名单里的板块」「输入里不是事件的和查不到的不算」；`SnapshotIndicators` 把数据源不可用换成网关的错误、其他错误原样抛出
- [x] 3.4 实现：`workflows/quant_numbers.py`；用 `uv run pytest tests/workflows` 验证

## 4. 交回给 PM 和 Critic、替身

- [x] 4.1 测试：agent-calls spec「对话的每一轮」的「Quant 的任务带量化指标条目」、「每个 agent 的输入和输出」的「子 agent 只看到 Task 和快照」；pm-calls spec「对话变成消息」的「工具结果带量化指标条目」；sub-agent-calls spec「子 agent 的上下文」的「行情带量比和成交量」；stand-ins spec「子 agent 替身」的「引用 Task 的输入」「Quant 引用量化指标条目」
- [x] 4.2 实现：`CaseFlow` 的 `_outcome` 带上条目；Quant 的替身引用条目；用 `uv run pytest tests/workflows tests/integrations` 验证

## 5. 装配

- [x] 5.1 测试：bootstrap spec「模拟盘的装配」的「排进的案件由替身跑到下单」（Quant 的任务存下条目，替身引用它）和「K 线取不到时 Quant 的任务失败」；全程序的测试用 K 线源的替身
- [x] 5.2 实现：`Externals.bar_history`，装配 `AlpacaBarHistory`、`BarService`、`IndicatorService`、`SnapshotIndicators`，接进 `AgentCallService`
- [x] 5.3 运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate update-quant-entry --strict`

## 6. 文档和冒烟

- [x] 6.1 完整版 Proposal「Task / Result 通信」「News」「Fundamentals」和「共享上下文」里子 agent 看到的「最新价和前收盘价」改成带量比和成交量；用 `rg` 确认没有别处还写着旧的说法
- [ ] 6.2 主机冒烟：提交部署以后，迁移 v0019 跑过；用部署的代码和生产 venv、真实 Alpaca，内存存储，不留东西：一个 NVDA 的日线 Quant 任务经网关存下条目、替身引用它，PM 的 `dispatch_tasks` 工具结果里有条目；看一次取条目的耗时
