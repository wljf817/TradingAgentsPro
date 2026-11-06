# Tasks

## 1. 格式

- [x] 1.1 先在 `tests/contracts/test_thesis.py` 写 `stock-pool` spec「承诺的格式」的测试，在 `tests/contracts/test_conclusion.py` 写 `case-types` spec「什么变了的格式」的测试；运行 `uv run pytest tests/contracts`，新测试失败
- [x] 1.2 `contracts/thesis.py` 加 `WatchCommitment` 和 `WatchThesis.commitment`，`contracts/conclusion.py` 加 `WatchConclusion.what_changed` 和它的检查；各处测试的基准关注论点带上承诺；运行 `uv run pytest tests/contracts` 通过

## 2. 股票池

- [x] 2.1 先在 `tests/modules/stockpool` 写 `stock-pool` spec「转入关注时次数清零」「计入时次数加 1」「没有新项的记录照常读回」的测试；运行后新测试失败
- [x] 2.2 `modules/stockpool/domain/entry.py` 加 `renewals`，升级和清仓转为关注时设为 0；`service.py` 的 `update_watch_thesis` 加 `counted`；运行 `uv run pytest tests/modules/stockpool` 通过（数据库测试要先 `open -a OrbStack`）

## 3. 续期上限

- [x] 3.1 先在 `tests/modules/scheduler/domain` 写 `case-types` spec「续期上限」全部场景的测试；运行后新测试失败
- [x] 3.2 `modules/scheduler/config.py` 加 `max_watch_renewals`，domain 加 `can_open` 和 `renewal_capped`，从模块包根导出；运行 `uv run pytest tests/modules/scheduler` 通过
- [x] 3.3 先写 `allowed_decisions` 到上限时去掉继续关注的测试，再加参数 `renewal_capped`；运行 `uv run pytest tests/modules/scheduler` 通过

## 4. 结论的校验

- [x] 4.1 先在 `tests/modules/scheduler/domain/test_validation.py` 写 `case-types` spec「结论的校验」新增场景的测试；运行后新测试失败
- [x] 4.2 `modules/scheduler/domain/case_type.py` 的 `CaseDefinition` 加 `category`，事件案件给出事件类别
- [x] 4.3 `modules/scheduler/domain/validation.py` 加三个拒绝原因和检查，`conclusion_rejection` 加 `renewal_capped`；运行 `uv run pytest tests/modules/scheduler` 通过
- [x] 4.4 `workflows/case_entries.py` 加原因文字"关注论点缺少承诺""续期次数已用完""没有写什么变了"；先写退回文字的测试，运行 `uv run pytest tests/workflows/test_case_entries.py` 通过

## 5. 案件工作流

- [x] 5.1 先在 `tests/workflows/test_case_flow.py` 写 `case-workflow` spec「到上限时没有继续关注」「开不了仓时还有继续关注」「到上限退回」「没写什么变了退回」「续期计入次数」「开不了仓的续期不计入」「平时调整不计入」的测试；运行后新测试失败
- [x] 5.2 `workflows/case_flow.py` 组案件上下文时按上限给允许的决定；`workflows/case_conclusion.py` 检查时传入是否到上限，生效时按续期和开得了仓给出 `counted`；运行 `uv run pytest tests/workflows` 通过

## 6. 替身

- [x] 6.1 先在 `tests/integrations/standin` 写 `stand-ins` spec「到上限时降为候选」「没有承诺的关注论点续期」和升级、催化剂复查带新内容的测试；运行后新测试失败
- [x] 6.2 `integrations/standin/conclusions.py` 写承诺和什么变了，允许的决定没有继续关注时降为候选；运行 `uv run pytest tests/integrations/standin` 通过

## 7. 文档和验证

- [x] 7.1 完整版 Proposal「观望」写明开不了仓时不计入、不限制
- [x] 7.2 确认新生成的 `config/settings.toml` 有续期上限这一项，默认值 3，已有的文件不用改
- [x] 7.3 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy` 和 `openspec validate update-watch-rules --strict`，全部通过
