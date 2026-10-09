# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [ ] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 13 条修改完整版 Proposal，每处只加或改一两句话：
  - 「触发 · 2. 定时心跳」检查项表下面：日期不晚于当天就算到期；同一只股票复查日和催化剂截止日期同时到期只开一个案件，用催化剂复查；这只股票已经有复查类案件在排队或运行时不再开（第 1、4 条）；同一次开出的案件按持仓、关注、候选评估、寻股、每周复盘的顺序入队（第 13 条）
  - 同一节检查项表的每周复盘一行："每周固定时间（默认每周最后一个交易日收盘后）"改为"每周最后一个交易日收盘后（下一个交易日在下一周）"（第 11 条）
  - 同一节"候选评估的上限"：新候选是加入股票池或代码触发的清仓转为候选以后、还没有候选评估得出结论的候选，PM 降为候选的不算；上限默认每天 3 个，在排队或运行的也算；没有评分的排在最后，评分相同的先过期的先开（第 5、6、7 条）
  - 同一节"故障处理"：顺延就是每次收盘检查都算到期、直到续期，已经在排队或运行的不重复开；不续期的结论没有执行成时同样再开（第 3、12 条）
  - 「股票池与寻股」寻股触发：有效候选是评分为空或不为负的候选，下限默认 5 只；评分 ≥ 1 的候选默认至少 2 只；临近过期是 3 个交易日以内，多数是超过一半；冷却期从上一次寻股得出结论的那一天算，没有结论的不算（第 9、10 条）
  - 「持仓与退出」清仓后的股票状态："当天收盘的定时心跳会为它开候选评估案件"改为它在当天收盘作为新候选，和其他新候选一起受每天的上限约束（第 8 条）
  - 「故障处理」降级模式："顺延到恢复后的第一个交易日"及其定义改为：降级期间收盘检查照常开案件，留在队列里，恢复以后按优先级运行；没有结论的事项下一次收盘检查再开（第 2、3 条）

  验证：七处和 proposal 的说法一致；`git diff` 里完整版 Proposal 没有其他改动

## 2. 配置

- [ ] 2.1 先写测试，改 `tests/modules/heartbeat/test_config.py`：spec「收盘检查的配置」的「默认配置」（在原来的默认配置测试里加五项）、「配置不合法」（五项各配置为 0）；另外检查五项取 1 都合法。验证：运行这个测试文件，确认新的断言因为配置项不存在而失败，原来的测试仍然通过
- [ ] 2.2 `modules/heartbeat/config.py` 的 `HeartbeatConfig` 增加五项（design D8），docstring 加上 close-check spec。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 3. 快照和复查类案件

- [ ] 3.1 先写测试：
  - `tests/modules/heartbeat/builders.py`：增加 spec「收盘检查的输入和输出」里的基准快照，以及"复查日改为""截止日期改为""评分改为""过期时间改为""是新候选""在排队或运行"等辅助函数（design D11）
  - 新建 `tests/modules/heartbeat/domain/test_close_check.py`，每个场景一个测试：「收盘检查的输入和输出」两个场景；「复查日和催化剂截止日期到期」五个场景；「在排队或运行的不重复开」四个场景；「没有结论时顺延」四个场景

  验证：运行这个测试文件，确认因为 `domain/close_check.py` 不存在而失败
- [ ] 3.2 新建 `modules/heartbeat/domain/close_check.py`：`CheckItem`、`ThesisDates`、`PoolCandidate`、`CloseSnapshot`、`close_check`（检查今天是交易日，先只接 `_reviews`）和 `_reviews`（design D1、D2、D3）；从 `__init__.py` 导出（测试只从包根 import），包根和 `domain/__init__.py` 的 docstring 加上收盘检查。验证：3.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 4. 候选评估

- [ ] 4.1 先写测试，写在 `test_close_check.py`，每个场景一个测试：「候选评估」七个场景。验证：运行，确认候选评估的测试因为还没有开候选评估而失败，第 3 组的测试仍然通过
- [ ] 4.2 实现 `_evaluations`、`_evaluation_order`，接进 `close_check`（design D5）。验证：4.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 5. 寻股

- [ ] 5.1 先写测试，写在 `test_close_check.py`，每个场景一个测试：「寻股」八个场景。验证：运行，确认寻股的测试因为还没有开寻股而失败，之前的测试仍然通过
- [ ] 5.2 实现 `_STRONG_SCORE`、`_exploration_due`，接进 `close_check`（design D6）。验证：5.1 和之前的测试都通过（「基准数据不开」仍然通过）；`uv run mypy`、`uv run ruff check` 通过

## 6. 每周复盘、开的顺序和改配置

- [ ] 6.1 先写测试，写在 `test_close_check.py`，每个场景一个测试：「每周复盘」四个场景；「开的顺序」的「全部检查项一起开」；「收盘检查的配置」的「改配置改变检查」。验证：运行，确认每周复盘的测试和「全部检查项一起开」因为还没有开每周复盘而失败
- [ ] 6.2 实现 `_weekly_due`，接进 `close_check`（design D7）。验证：6.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 性质测试

- [ ] 7.1 写 `tests/modules/heartbeat/domain/test_close_check_properties.py`（design D11）：随机快照和配置，检查 D11 列出的不变量，包括每个检查项都能构造成调度器的 `HeartbeatTrigger`。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/heartbeat` 通过
  - 用 `event` 确认样例里出现过七种检查项、在排队或运行挡住开的情况和候选评估被上限截断的情况
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 8. 整体检查

- [ ] 8.1 对照 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 8.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
