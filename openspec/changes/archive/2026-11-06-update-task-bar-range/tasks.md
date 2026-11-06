# Tasks

## 1. 格式

- [x] 1.1 先写 `task-dispatch` spec「任务请求的格式」「Task 的格式」新场景的测试，再在 `contracts/task.py` 加 `BarPeriod`、`BarWindow`、`TaskRequest` 和 `Task` 的 `bars` 和 validator；测试里的 Quant 请求都写上；运行 `uv run pytest tests/contracts tests/modules/gateway` 通过
- [x] 1.2 先写「读回没有周期和范围的任务」的测试，确认已有格式的 Task 读得出来；运行 `uv run pytest tests/modules/gateway` 通过

## 2. 网关

- [x] 2.1 先写「相同的任务」新场景的测试，再改 `same_task`；运行 `uv run pytest tests/modules/gateway` 通过
- [x] 2.2 先写「派发的顺序」「派发的配置」新场景的测试，再加 `GatewayConfig` 的四个上限、`TaskRejection` 和 `dispatch_rejection` 的检查；运行 `uv run pytest tests/modules/gateway` 通过

## 3. 替身

- [x] 3.1 先写 `stand-ins` spec「PM 替身派发任务」的测试，再让 Quant 的请求写日线、60 个交易日；运行 `uv run pytest tests/integrations/standin` 通过

## 4. 验证

- [x] 4.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy` 和 `uv run lint-imports`，全部通过
