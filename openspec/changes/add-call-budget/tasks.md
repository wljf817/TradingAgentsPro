# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [ ] 1.1 人同意 proposal「需要人决定」以后，按已决定第 1 条和新发现的第 4 到 7、9、11 条修改完整版 Proposal，每处只加或改一两句话（已决定第 2、3 条 Proposal 已经写了；第 8 条是默认值，第 10 条是实现，都不写进 Proposal）：
  - 「调度器与运行模块」运行模块表的 Agent 网关一行："检查白名单、任务数、轮次和预算"改为"检查白名单、任务数和轮次，记下每次模型调用的费用"（第 4 条）
  - 「调度器与运行模块」运行约束的每日模型调用预算一条："（Agent 网关执行）"改为"（Agent 网关记账）"；这一条后面加：预算按美元费用算，每次调用的费用是模型的单价乘 token 用量；持仓案件的一次调用在普通预算还没用完时整笔记普通预算，用完（不小于额度）以后记持仓保留预算（已决定第 1 条，第 5、6、7 条）。预算只在案件开始时由队列和健康监控看，已经开始的案件照常跑完，预算可以超出一点（第 4 条）
  - 「Task / Result 通信」Result 一行："耗时和费用"后面加"（费用是这个任务各次模型调用的费用之和）"（第 11 条）；规则第三条"并受并发和调用预算约束"改为"并受并发约束；调用预算只在案件开始时检查，不拒绝已经开始的案件的任务"（第 4 条）
  - 「触发 · 3. 自定义跟进」其他规则："每天的申请次数有上限，计入调用预算"改为"每天的申请次数有上限"，并加"跟进案件的模型调用按案件类型计入调用预算"（第 9 条）
  - 「架构」决策层的 Agent 网关一条："检查白名单、任务数、轮次和预算"和第一项一样改（第 4 条）

  验证：各处和 proposal 的说法一致；`git diff` 里完整版 Proposal 只有这五处有改动

## 2. token 用量

- [ ] 2.1 先写测试，补在 `tests/contracts/test_model.py`：`TokenUsage` 输入、输出都是 0 合法；输入 -1、输出 -1、输出 1.5 都报错；frozen、不接受多余的项。验证：运行这个测试文件，确认因为 `TokenUsage` 不存在而失败
- [ ] 2.2 在 `contracts/model.py` 加 `TokenUsage`（design D2），模块 docstring 写上它；`contracts/task.py` 的 `Result.cost` 注释改为"这个任务各次尝试记下的费用之和，见 call-budget spec「子 agent 任务的费用」"（design D7）。验证：2.1 的测试通过；`uv run mypy`、`uv run pytest tests/architecture` 通过

## 3. 单价和额度的配置

- [ ] 3.1 先写测试：
  - `tests/modules/gateway/builders.py`：spec 的基准单价（p-1、p-2 是 2 和 10，p-3 是 1 和 4，c-1、c-2 是 1.25 和 10，s-1、s-2 是 0.3 和 2.5），`assignments()` 默认带上，`changes` 可以用 `prices=` 覆盖
  - `tests/modules/gateway/test_config.py`：call-budget spec「模型的单价」三个场景，`price()` 取得基准单价、取不在配置里的模型抛 `KeyError`；「预算的配置」的「默认配置」「配置不合法」

  验证：运行 `tests/modules/gateway/test_config.py`，确认新的测试因为 `ModelPrice` 和三个额度不存在而失败
- [ ] 3.2 实现（design D3、D10）：`config.py` 加 `ModelPrice`；`ModelAssignments` 加 `prices`、`price()` 和两项检查；`GatewayConfig` 加 `general_budget`、`reserve_budget`、`exploration_budget`，docstring 写上 call-budget spec「预算的配置」；`__init__.py` 导出 `ModelPrice`。验证：3.1 的测试通过；model-fallback 原有的配置测试仍然通过，并且报错的信息还是原来的原因；`uv run pytest tests/modules/gateway`、`uv run mypy` 通过

## 4. 预算的规则

- [ ] 4.1 先写测试 `tests/modules/gateway/domain/test_budget.py`：
  - `cost_of`：spec「按单价算」「不舍入」两个场景
  - `charged_budget`：逐个检查 14 种案件类型，在普通预算用完和没用完两种情况下记到的预算，和 spec「费用记到哪个预算」的表一致
  - `budget_status`：没有记录时三个预算都是 0、都没用完，交易日原样给出；三个预算各自在额度减 0.01 和正好等于额度时的用完与否；超出额度仍算用完；三个预算互不影响；用较小的额度配置也成立

  验证：运行这个测试文件，确认因为 `domain/budget.py` 不存在而失败
- [ ] 4.2 实现 `domain/budget.py`（`Budget`、`CallCost`、`BudgetStatus`、`cost_of`、`charged_budget`、`budget_status`，design D4），并从 `__init__.py` 导出（测试只从包根 import）。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 5. 存储和用例

- [ ] 5.1 先写测试：
  - `tests/fakes/costs.py`：`InMemoryCostStore`，按写入的先后存在 list 里（design D8）
  - `tests/modules/gateway/builders.py`：K-5 到 K-8 的 `CaseScope`；`spend(service, case, x)` 是 spec 的"K-n 记下 x"；`budget_service(clock=None, store=None, **config)`（design D13）
  - `tests/modules/gateway/test_budget_service.py`，每个场景一个测试：
    - 「费用的记录」三个场景
    - 「费用的算法」的「用量不合法」：构造用量报错，存储里没有新的记录
    - 「费用记到哪个预算」六个场景
    - 「每日的划分」五个场景
    - 「预算用完」四个场景
    - 「预算的配置」的「改成别的值」

  验证：运行这个测试文件，确认因为 `CostStore`、`CallBudgetService` 不存在而失败
- [ ] 5.2 实现（design D5、D8、D9）：`ports.py` 加 `CostStore`；`service.py` 加 `CallBudgetService`（`record`、`status`、`_status`）；`__init__.py` 导出 `CostStore`、`CallBudgetService`，模块 docstring 加上调用预算和 call-budget spec。验证：5.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 已经开始的案件和子 agent 任务的费用

- [ ] 6.1 写测试，补在 `test_budget_service.py`，每个场景一个测试，用 `CallBudgetService` 和已有的 `TaskDispatchService`、`ModelFallbackService` 一起驱动：
  - 「已经开始的案件」四个场景：预算用完以后派发照常、记下照常超出额度、PM 的调用仍从 p-1 开始
  - 「子 agent 任务的费用」两个场景：各次 `record` 的 `cost` 之和交给 `receive`、`expire`、`fail`，Result 的费用分别是 0.0073、0.0024、0

  验证：测试通过，并且 `TaskDispatchService`、`ModelFallbackService` 和 `domain/dispatch.py`、`domain/fallback.py` 都没有改动（design D6、D7）
- [ ] 6.2 case-stages 的 delta spec「系统故障」：`tests/modules/scheduler/domain/test_stage.py` 的 `test_pm_call_fails` 说明去掉"预算用完被拒绝"，只写主备模型都超时（design D6）。验证：`uv run pytest tests/modules/scheduler/domain/test_stage.py` 通过；`git diff src/tradingagentspro/modules/scheduler` 为空

## 7. 状态机性质测试

- [ ] 7.1 写 `tests/modules/gateway/test_budget_state_machine.py`：Hypothesis 的 `RuleBasedStateMachine`，规则、参考模型和每一步之后的不变量按 design D13。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/gateway/test_budget_state_machine.py` 通过
  - 用 `event` 确认样例里出现过：三个预算各自用完、持仓案件记到持仓保留预算、跨过收盘恢复、跨过周末或休市日、重启
  - 临时把 `budget_status` 的比较改成 `>`、把 `_status` 的起点改成 `session_closing_after(now).open`，分别确认状态机能找到反例，再改回来

## 8. 整体检查

- [ ] 8.1 对照两份 delta spec（call-budget、case-stages），确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 8.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
