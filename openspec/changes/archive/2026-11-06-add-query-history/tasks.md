# Tasks

## 1. 契约和枚举

- [x] 1.1 `OrderStatus`、`TradeKind` 移到 `contracts`，`execution` 从那里导入（design D9）；运行 `uv run pytest tests/modules/execution tests/contracts` 和 `uv run mypy` 通过
- [x] 1.2 新增 `contracts/history.py`，`CaseContext` 加 `history`；先写 `agent-calls` spec「案件上下文的历史索引」的测试；运行 `uv run pytest tests/contracts` 通过

## 2. 存储

- [x] 2.1 先写 `state-store` spec「读取某个时刻的版本」的测试，再写 `VersionedStore.as_of`；需要 OrbStack；运行 `uv run pytest tests/modules/statestore` 通过
- [x] 2.2 写迁移 `v0015`，`TRADES` 加 `symbol`，`PostgresTradeStore.save` 写入；先写 `trade-records` spec「按股票读某个时刻的交易记录」的测试，再写 `TradeStore.at` 和 `tests/fakes` 的实现；运行 `uv run pytest tests/modules/execution tests/bootstrap` 通过，确认已有的行回填了股票
- [x] 2.3 先写 `case-records` spec「一只股票已结束的全部案件」的测试，再写 `CaseRecordStore.ended`、服务方法和 `tests/fakes` 的实现；运行 `uv run pytest tests/modules/caserecords` 通过

## 3. 已实现盈亏和历史

- [x] 3.1 先写 `history-query` spec「已实现盈亏」的测试，再写 `execution` 的 domain 函数和 `ExecutionService.trades_at`（design D6）；运行 `uv run pytest tests/modules/execution` 通过
- [x] 3.2 先写 `history-query` spec「查询的结果」「时间截止」和「起始日」「案件类型」「没有记录的股票」「刚好等于条数上限」「超过条数上限」的测试，再写 `workflows/case_history.py`（`report`、`index`）；运行 `uv run pytest tests/workflows` 通过
- [x] 3.3 配置加 `max_history_queries`、`max_history_rows`；先写 `history-query` spec「配置不合法」的测试；端口 `HistoryReader`、`Action.QUERY_HISTORY`；先写 `program-log` spec「查询历史」的测试，再写 `HistoryQueryService`；运行 `uv run pytest tests/modules/gateway tests/modules/programlog` 通过

## 4. 接上 PM

- [x] 4.1 先写 `history-query` spec「研究时可以查询」「回应时可以查询」「查别的股票」「寻股案件查询」「查询结果不进对话」「次数用完」「下一次唤醒重新计」和 `program-log` spec「次数用完不写」的测试，以及 `pm-calls` spec「交回工具」改过的场景（用 `tests/integrations/llm/server.py`）；再写 `pm.py` 的工具和 `LlmBackend` 的参数；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 4.2 先写 `case-workflow` spec「历史索引」的测试，再改 `CaseServices`、`CaseFlow.context`；运行 `uv run pytest tests/workflows` 通过
- [x] 4.3 `bootstrap/app.py` 装配 `CaseHistory`、`HistoryQueryService`，传给 `_model_backend` 和 `CaseServices`；生成的设置文件带上两个新配置（`settings-file` spec「文件的内容」）；运行 `uv run pytest tests/bootstrap` 通过
- [x] 4.4 全部检查：运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 文档和冒烟

- [x] 5.1 完整版 Proposal「查询工具」：次数上限改成按每次运行算，写明结果不跨唤醒；`grep 每次运行` 能在这一节查到
- [x] 5.2 部署以后在服务器上用真实模型冒烟：三个模型各跑一次研究和回应，模型调用 `query_history` 后能交回结果，日志有查询历史的条目
