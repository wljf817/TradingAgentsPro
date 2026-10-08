# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [ ] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 9 条修改完整版 Proposal，每处只加或改一两句话：
  - 「调度器与运行模块」的排队规则加一条：案件类型在开始运行时按当时的股票状态确定；定时心跳的检查项针对一个状态，开始时股票已经不在这个状态，或者事件和跟进案件的股票已经不在股票池也不是持仓（跟进还包括已经是候选），案件不再运行（第 1、6 条）
  - 「触发」开头的两张表后面：每份结论有一个主决定，论点更新（一份完整的新论点）和跟进申请附在持有、加仓、减仓、继续关注上；"复查类案件里选择持有或继续关注，就是续期"改为"选择持有、加仓、减仓或继续关注"（第 2、4、5 条）
  - 「通用规则」的研究要求：开仓方案包括加仓，只算状态是完成的 Result（第 3、8 条）
  - 「Task / Result 通信」的最低要求：各类事件至少派给的子 agent（价格类给 Quant，新闻给 News，公告和财报给 Fundamentals），派过任务就算（第 7、8 条）
  - 「Agent」的 PM 的交易方案："目标仓位"改为"股数（清仓不写）"（risk-rules 已决定的第 1 条）；开仓和加仓必须写紧急阈值，有效期和允许的价格偏离可以不写（第 9 条）

  验证：五处和 proposal 的说法一致；`git diff` 里完整版 Proposal 没有其他改动

## 2. contracts：子 agent、事件类别、案件类型、投资论点和方案字段的检查

- [ ] 2.1 先写测试 `tests/contracts/test_thesis.py`，增加 spec「投资论点的格式」的四个场景，基准投资论点和 spec 一致，格式错误断言抛出 `ValidationError`。验证：运行这个测试文件，确认新测试因为 `InvestmentThesis` 不存在而失败，原有的关注论点测试不受影响
- [ ] 2.2 新增 `contracts/agent.py`（`SubAgent`）、`contracts/event.py`（`EventCategory`）、`contracts/case.py`（`CaseType`），在 `contracts/thesis.py` 增加 `InvestmentThesis`，写法见 design D1、D2。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过
- [ ] 2.3 在 `contracts/trade_plan.py` 把 `TradePlan` 按动作检查字段的规则抽成 `PlanFields` 和 `check_plan_fields`（design D4），`TradePlan` 的 validator 改为调用它，错误信息不变。验证：`tests/contracts/test_trade_plan.py` 不改、全部通过；`uv run pytest tests/modules/risk tests/modules/emergency tests/modules/execution` 通过；`uv run mypy` 通过

## 3. contracts：结论

- [ ] 3.1 先写测试 `tests/contracts/test_conclusion.py`，每个场景一个测试，基准数据和 spec 一致：
  - 「结论的公共部分」三个场景
  - 「持仓的结论」八个场景，「关注的结论」四个场景，「候选的结论」三个场景，「寻股的结论」四个场景
  - 「PM 写的交易方案」五个场景，其中「变成交易方案」断言得到的 `TradePlan` 和逐字段构造的相等
  - 「跟进申请的格式」四个场景
  - 每种结论的 JSON schema 里，决定只列出这个状态允许的值

  验证：运行这个测试文件，确认因为 `contracts/conclusion.py` 不存在而失败
- [ ] 3.2 新增 `contracts/conclusion.py`：`Decision`、`AfterClose`、`PlanDraft`、`FollowUpReason`、`FollowUpRequest`、`PoolAddition`、`PoolRemoval`、四种结论和 `Conclusion`，以及持仓、关注结论的 `trade_plan`（design D3、D4、D5）。验证：3.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 4. 调度器模块和配置

- [ ] 4.1 先写测试 `tests/modules/scheduler/test_config.py`：spec「研究要求的配置」的「默认配置」「配置不合法」两个场景。同时建好 `tests/modules/scheduler/__init__.py`、`tests/modules/scheduler/domain/__init__.py`。验证：运行这个测试文件，确认因为模块不存在而失败
- [ ] 4.2 新建 `src/tradingagentspro/modules/scheduler/`：`__init__.py`（模块说明，导出 `SchedulerConfig`）、`config.py`（`SchedulerConfig`，design D8）、`domain/__init__.py`。验证：4.1 的测试通过；`uv run lint-imports` 和 `uv run pytest tests/architecture` 通过，module-layers 契约包括新模块

## 5. 触发和案件类型

- [ ] 5.1 先写测试：
  - `tests/modules/scheduler/builders.py`：spec 的基准数据（基准投资论点、基准关注论点、基准方案条款、基准跟进申请、结论的公共部分、R-1 和 R-2、派过任务的子 agent）
  - `tests/modules/scheduler/domain/test_case_type.py`，每个场景一个测试：「触发」一个场景，「案件类型」十五个场景，「开始运行时确定案件类型」八个场景，「事件要求的子 agent」四个场景；每个案件类型断言 `conclusion_model` 给出的结论种类
  - 同一文件里的性质测试（design D11）：任意触发和状态下，针对单只股票的类型针对的状态就是传入的状态，优先级和 spec 的表一致，结论种类和状态一致

  验证：运行这个测试文件，确认因为 `domain/case_type.py` 不存在而失败
- [ ] 5.2 实现 `domain/case_type.py`（触发、`CaseDefinition`、`define_case`、`conclusion_model` 和两张表，design D6），并从 `__init__.py` 导出（测试只从包根 import）。验证：5.1 的测试通过；`HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/scheduler` 通过；`uv run mypy`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 6. 结论的校验

- [ ] 6.1 先写测试 `tests/modules/scheduler/domain/test_validation.py`，每个场景一个测试：
  - 「结论的校验」十三个场景，包括多项不通过时给出第一项和复盘案件报错
  - 「研究要求的配置」的「改成 1 个或 3 个」
  - 性质测试（design D11）：在通过研究要求的开仓方案、加仓、升级结论上多引用一个已完成的 Result，不会变成"研究不足"

  验证：运行这个测试文件，确认因为 `domain/validation.py` 不存在而失败
- [ ] 6.2 实现 `domain/validation.py`（`ConclusionRejection`、`conclusion_rejection`，design D7），并从 `__init__.py` 导出。验证：6.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 整体检查

- [ ] 7.1 对照 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 7.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
