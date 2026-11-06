# Tasks

## 1. 结论的校验

- [x] 1.1 先在 `tests/modules/scheduler/domain/test_validation.py` 写 `case-types` spec「开仓评分的边界」「加仓的评分」「其他决定不看评分」「评分排在研究要求之前」的测试；运行 `uv run pytest tests/modules/scheduler/domain/test_validation.py`，新测试失败
- [x] 1.2 `modules/scheduler/domain/validation.py` 加拒绝原因和检查，排在研究要求之前；运行 `uv run pytest tests/modules/scheduler` 通过
- [x] 1.3 `workflows/case_entries.py` 加原因文字"开仓评分不大于 0"；先写退回文字的测试，运行 `uv run pytest tests/workflows/test_case_entries.py` 通过

## 2. 验证

- [x] 2.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check` 和 `uv run mypy`，全部通过
