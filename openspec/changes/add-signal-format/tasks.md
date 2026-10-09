# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [x] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 10 条修改完整版 Proposal「Agent」的「信号统一格式」一段，每处只加或改一两句话：
  - "评分 -2 到 +2，每档有明确定义"后面：评分是整数，比较的是评估窗口内相对板块 ETF 的超额收益；置信度是这个判断成立的把握，和评分分开（第 4 条）
  - "允许「无信号」"后面：无信号只写原因，可以引用看过的条目；中性 0 是一个判断，数据不足时答无信号（第 2 条）
  - "注明数据时效"改为"注明数据截至的时刻，不能晚于收到输出的时刻"（第 3 条）
  - "引用所依据的状态库条目 ID；附理由和主要风险"改为至少引用一条，理由和主要风险都必填（第 5 条）
  - "格式不合法视为无效"后面：评估窗口不在配置的选项里也算，只在收到输出时检查，改配置不影响已经写入的信号；子 agent 的 Result 记为「失败」并注明格式不合法，不切换模型（第 1、6 条）
  - PM 的评分一句：是同样格式的信号，自带理由、风险和引用，不能是无信号，其中的评分作为最新评分；Agent 网关同样检查评估窗口和数据截至时刻，不合法时整份结论格式不合法（第 2、7、8、10 条）
  - 加一句：一个信号只针对一只股票，子 agent 的信号针对 Task 指定的股票（第 9 条）

  验证：各处和 proposal 的说法一致；`git diff` 里完整版 Proposal 只有这一段有改动

## 2. contracts：信号和无信号

- [x] 2.1 先写测试 `tests/contracts/test_signal.py`，每个场景一个测试，基准数据和 spec 一致：
  - 「信号的格式」九个场景：格式错误断言抛出 `ValidationError`；「缺少一项」断言错误的位置就是缺少的那一项；「构造以后不能修改」断言赋值报错、信号不变
  - 「评分的刻度」两个场景
  - 「无信号」四个场景：子 agent 的输出用 `TypeAdapter(SubAgentOutput)` 解析，断言得到的类型
  - 性质测试（design D8）：任意格式正确的信号和无信号按 JSON 写出再用 `SubAgentOutput` 读回，类型相同、对象相等

  验证：运行这个测试文件，确认因为 `contracts/signal.py` 不存在而失败
- [x] 2.2 新增 `contracts/signal.py`：`EntryId`、`Signal`、`NoSignal`、`SubAgentOutput`（design D1），模块 docstring 写明 signal-format spec。验证：2.1 的测试通过；`HYPOTHESIS_PROFILE=ci uv run pytest tests/contracts/test_signal.py`、`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. contracts：结论里的信号

- [x] 3.1 先改测试，评分换成信号，用基准信号：
  - `tests/contracts/test_conclusion.py`：公共部分的 `score` 改为 `signal`；按 case-types delta 改「评分的边界」（信号的评分 -2、2 正确，-3、3、1.5 和不写信号错误），新增「信号的格式错误或者是无信号」；「增删股票池」用评分 1 和 2 的信号，断言每只候选信号里的评分；「加入的候选必须有评分」加上信号是无信号；寻股的结论写了自己的 `signal` 是格式错误
  - `tests/modules/scheduler/builders.py` 的 `_COMMON` 和 `exploration`、`tests/modules/scheduler/domain/test_validation.py` 里的 `PoolAddition`：`score` 改为 `signal`，测试本身不变

  验证：运行 `uv run pytest tests/contracts/test_conclusion.py tests/modules/scheduler`，确认因为结论的字段还叫 `score` 而失败
- [x] 3.2 修改 `contracts/conclusion.py`：持仓、关注、候选的结论和 `PoolAddition` 的 `score: int` 改为 `signal: Signal`（design D3），模块和类的 docstring 同步。验证：3.1 的测试通过；`uv run pytest tests/contracts tests/modules/scheduler tests/modules/stockpool tests/modules/heartbeat` 通过；`grep -rn "\.score" src` 只剩股票池和收盘检查自己的评分；`uv run mypy` 通过

## 4. Agent 网关模块和配置

- [x] 4.1 先写测试 `tests/modules/gateway/test_config.py`：spec「信号的配置」的「默认配置」「配置不合法」两个场景；另外检查只有一个选项也合法。同时建好 `tests/modules/gateway/__init__.py`、`tests/modules/gateway/domain/__init__.py`。验证：运行这个测试文件，确认因为模块不存在而失败
- [x] 4.2 新建 `src/tradingagentspro/modules/gateway/`：`__init__.py`（模块说明，导出 `GatewayConfig`）、`config.py`（`GatewayConfig`，design D5）、`domain/__init__.py`。验证：4.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过，module-layers 契约包括新模块

## 5. 收到输出时的校验

- [x] 5.1 先写测试：
  - `tests/modules/gateway/builders.py`：基准信号、基准无信号、现在 2026-10-14 15:00Z；`signal(**changes)` 只改基准信号的几项；用 case-types 的结论的公共部分构造持仓、关注、候选和寻股的结论（design D8）
  - `tests/modules/gateway/domain/test_signal.py`，每个场景一个测试：「收到输出时的校验」六个场景（「改配置不影响已经写入的信号」用写入的各项重新构造信号，断言相等）；「结论里的信号」三个场景；「信号的配置」的「改成别的选项」

  验证：运行这个测试文件，确认因为 `domain/signal.py` 不存在而失败
- [x] 5.2 实现 `domain/signal.py`（`SignalRejection`、`signal_rejection`、`conclusion_signal_rejection`，design D4），并从 `__init__.py` 导出（测试只从包根 import），docstring 写明 signal-format spec。验证：5.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 6. 整体检查

- [x] 6.1 对照两份 delta spec（signal-format、case-types），确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 6.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
