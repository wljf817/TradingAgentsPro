# Tasks

## 1. 模型文件

- [x] 1.1 先写 `model-config` spec「文件的内容」「文件出错」的测试（`tests/bootstrap/test_models.py`，用临时文件），再加 `bootstrap/models.py`；运行 `uv run pytest tests/bootstrap/test_models.py` 通过
- [x] 1.2 加 `bootstrap/models.toml`：六个 agent 用「替身的模型」的表，单价 0，额度 20、10、5；写「仓库里的模型文件」的测试，运行 `uv run pytest tests/bootstrap/test_models.py` 通过

## 2. 装配

- [x] 2.1 先写 `model-config` spec「额度来自文件」「其他项用默认值」和 `bootstrap` spec「模型文件出错」的测试，再给 `BootstrapConfig` 加 `models_file`，`start` 在装配这一步最先读文件，把结果传给 `_assemble`、`_case_side`；运行 `uv run pytest tests/bootstrap` 通过
- [x] 2.2 改 `bootstrap` spec「启动时持仓保留预算已经用完」的测试，费用从配置的额度取，不写固定的 1.00；运行 `uv run pytest tests/bootstrap` 通过

## 3. 替身

- [x] 3.1 把 `stand_in_models()` 挪到 `tests/fakes/models.py`，更新用到它的测试，删掉 `integrations/standin/config.py` 里的函数和厂商常量，改 `integrations/standin/__init__.py` 的说明；`grep -rn stand_in_models src` 没有结果，运行 `uv run pytest tests/integrations/standin tests/workflows tests/bootstrap` 通过

## 4. 验证

- [x] 4.1 运行 `uv build --wheel -o <临时目录>`，`unzip -l` 能看到 `tradingagentspro/bootstrap/models.toml`
- [x] 4.2 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check` 和 `uv run mypy`，全部通过
