# Tasks

## 1. 系统宪章

- [x] 1.1 `contracts` 加 `Charter`（正整数版本、非空文字）；运行 `uv run mypy` 通过
- [x] 1.2 先写 `shared-context` spec「系统宪章」的测试（`tests/bootstrap/test_charter.py`），再写 `bootstrap/charter.toml` 和 `bootstrap/charter.py`，文字只放占位；运行 `uv run pytest tests/bootstrap` 通过

## 2. 案件上下文的两项

- [x] 2.1 `CaseContext` 加 `decisions`、`research_agents`；先写 `case-workflow` spec「案件上下文」的新场景，再改 scheduler 的 domain 和 `CaseFlow.context`；更新替身和 workflows 的测试构造；运行 `uv run pytest tests/modules/scheduler tests/workflows tests/integrations/standin` 通过

## 3. PM 的上下文

- [x] 3.1 `contracts` 加 `PmContext`、`NewsHeadline`、`FilingHeadline`；运行 `uv run mypy` 通过
- [x] 3.2 先写 `shared-context` spec「PM 的上下文」「新闻和公告的标题」「事实的范围」的测试（`tests/modules/gateway/domain/test_context.py`），再写 `modules/gateway/domain/context.py` 并从网关包根导出；运行 `uv run pytest tests/modules/gateway` 通过

## 4. 唤醒日志

- [x] 4.1 先写 `program-log` spec「调度器的动作」的新场景，再改 `case_entries.py` 的 `wake_text`、`wake_failed_text` 和 `CaseFlow._called`，`CaseFlow` 收 `charter_version`；运行 `uv run pytest tests/workflows` 通过
- [x] 4.2 `bootstrap/app.py` 组出宪章，把版本传给 `CaseFlow`；运行 `uv run pytest tests/bootstrap` 通过

## 5. 文档和验证

- [x] 5.1 完整版「PM」看到的内容加上这只股票的财报日历和 surprise；`grep surprise` 能在「PM」一节查到
- [x] 5.2 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy` 和 `uv run lint-imports`，全部通过
