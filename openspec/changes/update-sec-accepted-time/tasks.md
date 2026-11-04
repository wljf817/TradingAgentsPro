# Tasks

## 1. 受理时刻

- [x] 1.1 把 `tests/integrations/sec/payloads.py` 的 acceptanceDateTime 改成 spec 基准数据的值，把 `tests/integrations/sec/test_edgar.py` 的两个受理时刻测试改成 spec「受理时刻」的场景；运行 `uv run pytest tests/integrations/sec/test_edgar.py`，确认这些测试先失败
- [x] 1.2 `integrations/sec/edgar.py` 按 UTC 直接读取 acceptanceDateTime，去掉美东时区；运行 `uv run pytest tests/integrations/sec/test_edgar.py` 全部通过
- [x] 1.3 更新 `tests/integrations/sec/test_edgar_smoke.py` 开头对受理时刻的说明；运行 `uv run ruff check` 和 `uv run mypy` 通过

## 2. 验证

- [x] 2.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check` 和 `uv run mypy`，全部通过
- [x] 2.2 在服务器上用测试副本运行 `uv run pytest -m news_sources tests/integrations/sec/test_edgar_smoke.py`，受理时刻的断言通过
