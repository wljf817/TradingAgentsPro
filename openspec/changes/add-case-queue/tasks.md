# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [x] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 10 条以及风控规则「需要人决定」第 5 条、紧急风险退出「需要人决定」第 5 条修改完整版 Proposal，每处只加或改一两句话：
  - 「调度器与运行模块」运行约束的并发上限：只计还在调用模型的案件，等人工确认、等下单的案件不占名额，但仍占着这只股票（第 5 条）
  - 同一节运行约束的每日调用预算：普通预算用完后其余案件留在队列里，下一个交易日预算恢复后按优先级运行；寻股只看单独的寻股预算，复盘用普通预算（第 2 条）
  - 同一节排队规则的第一条：寻股同一时间也只跑一个（第 6 条）
  - 排队规则的"同优先级按触发时间先后"：触发时间相同按入队的先后（第 10 条）
  - 排队规则的"案件不再运行"：排队期间按当时的状态算优先级，一旦确定不了类型就不再运行，不等轮到它（第 9 条）
  - 排队规则的作废一条改为：这只股票有复盘以外的新案件在排队时，它已经交给人工确认、还没提交订单的开仓类方案（等确认，或已确认、等下单）作废，预留随之释放；还在 PM、Critic 和风控阶段的不作废。退出类方案仍然不作废（风控规则第 5 条，第 3、4 条）
  - 「故障处理」降级模式加一条：不开始新案件，新的触发照常排队；"恢复后先对账，再按优先级补跑积压的持仓案件"改为运行积压的全部案件，持仓案件在前，非持仓的也不丢（第 1 条）
  - 「故障处理」紧急风险事件例外：同时作废这只股票还没提交订单的方案（开仓类和退出类）并释放预留；复盘案件不中止；排队的案件不动，等清仓卖单结束后按新的状态运行（紧急风险退出第 5 条，第 7、8 条）

  验证：八处和 proposal 的说法一致；`git diff` 里完整版 Proposal 没有其他改动

## 2. 配置

- [x] 2.1 先写测试 `tests/modules/scheduler/test_config.py`，增加 spec「队列配置」的「默认配置」「配置不合法」两个场景。验证：运行这个测试文件，确认新测试因为没有 `max_concurrent_cases` 而失败，研究要求的配置测试不受影响
- [x] 2.2 `SchedulerConfig` 增加 `max_concurrent_cases`（design D8）。验证：2.1 的测试和已有的调度器测试通过；`uv run mypy` 通过

## 3. 快照、决定和启动的顺序

- [x] 3.1 先写测试：
  - `tests/modules/scheduler/builders.py` 增加 spec 的基准数据（design D11）：五只股票的状态，场景里用到的触发和触发时刻，P-1 到 P-3，固定的案件 ID，新建快照的辅助函数（默认没有运行中的案件和方案、不降级、预算没用完）
  - `tests/modules/scheduler/domain/test_queue.py`，每个场景一个测试：「队列的输入和决定」两个场景，「排队的案件按现在的状态确定类型」三个场景，「启动的顺序」三个场景，「并发上限」四个场景，「队列配置」的「改成 1」

  验证：运行这个测试文件，确认因为 `domain/queue.py` 不存在而失败
- [x] 3.2 实现 `domain/queue.py`：`PlanStage`、快照和决定的 dataclass（design D2），`queue_decision` 按现在的状态确定类型、不再运行、按顺序在名额内启动（design D3、D4、D5；这一步作废先返回空）；从 `__init__.py` 导出（测试只从包根 import）。验证：3.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 4. 启动的限制：同一只股票、降级和预算

- [x] 4.1 先写测试，写在 `test_queue.py`，每个场景一个测试：「同一只股票一次一个案件」六个场景，「降级模式」的「降级期间不启动」「恢复后按优先级运行积压的案件」，「调用预算」六个场景；另外逐个检查 14 种案件类型在两个预算标志下能不能启动，和 spec「调用预算」的表一致。验证：运行，确认忙的股票、紧急退出、寻股一次一个、降级和预算的测试失败
- [x] 4.2 在 `_starts` 实现占位（复盘以外的案件占股票，寻股占 `None`，正在紧急退出的股票）、降级模式和预算（design D4）。验证：4.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 5. 作废

- [x] 5.1 先写测试，写在 `test_queue.py`，每个场景一个测试：「新案件排队时作废开仓方案」十个场景，「降级模式」的「降级期间照常不再运行和作废」。验证：运行，确认作废的测试失败、不作废的测试通过
- [x] 5.2 实现 `queue_decision` 的作废（design D6）。验证：5.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 6. 紧急退出

- [x] 6.1 先写测试，写在 `test_queue.py`，每个场景一个测试：「紧急退出」五个场景。验证：运行，确认因为 `emergency_decision` 不存在而失败
- [x] 6.2 实现 `emergency_decision`（design D7），并从 `__init__.py` 导出。验证：6.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 性质测试

- [x] 7.1 写 `tests/modules/scheduler/domain/test_queue_machine.py`（design D11）：Hypothesis 的 `RuleBasedStateMachine` 随机交错入队、案件结束、离开调用模型的阶段、换并发上限、方案的产生和进入下一个阶段、状态变化、紧急退出和结束、降级和两个预算标志，每次变化以后取得并执行队列的决定，检查 D11 列出的不变量。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/scheduler` 通过
  - 用 `event` 确认样例里出现过：名额用完、股票忙被跳过、紧急退出期间被跳过、寻股被跳过、预算挡住、降级挡住、确定不了类型、作废开仓方案、紧急退出中止案件
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 8. 整体检查

- [x] 8.1 对照 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 8.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
