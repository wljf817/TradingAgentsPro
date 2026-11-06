# Tasks

## 1. 风控算额度

- [x] 1.1 `contracts/risk.py` 加 `Headroom`：可用资金、持仓名额（可以没有）、单只仓位上限、单笔风险上限、最小下单额；运行 `uv run mypy` 通过
- [x] 1.2 先写 `case-headroom` spec「可用额度的内容」「额度和风控一致」的测试（`tests/modules/risk/domain/test_headroom.py`，一致性的场景用真的 `evaluate_plan`），再写 `modules/risk/domain/headroom.py`；运行 `uv run pytest tests/modules/risk` 通过
- [x] 1.3 `RiskService.headroom` 读全部有效的开仓预留，先写「算额度不改变预留」的测试；运行 `uv run pytest tests/modules/risk` 通过

## 2. 快照带上额度

- [x] 2.1 `contracts/snapshot.py` 的 `Snapshot` 加带默认值的 `headroom`，不进 `entries()`；写「旧快照没有额度」的测试，运行 `uv run pytest tests/contracts` 通过
- [x] 2.2 先写 `state-snapshot` spec「可用额度」的测试，再改 `workflows/snapshot_flow.py`，`SnapshotFlow` 接收风控服务；更新 `tests/workflows/snapshot_builders.py`，运行 `uv run pytest tests/workflows` 通过
- [x] 2.3 `bootstrap/app.py` 把风控服务传给 `SnapshotFlow`；运行 `uv run pytest tests/bootstrap` 通过

## 3. 文档和验证

- [x] 3.1 完整版 Proposal「PM」看到的内容里，可用额度加上最小下单额；`grep 最小下单额` 能查到
- [x] 3.2 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check` 和 `uv run mypy`，全部通过
