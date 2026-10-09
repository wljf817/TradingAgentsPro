# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [ ] 1.1 人同意 proposal「需要人决定」以后，按第 2 到 13 条修改完整版 Proposal「Task / Result 通信」，每处只加或改一两句话（第 1 条「信号统一格式」已经写明，不再改）：
  - 字段表：Task 加"股票"，截止时间由网关按任务时限给出（第 6、7 条）；Result 的输出完成时是信号、无信号时是无信号，失败和被拒绝带原因，一个 Task 只有一个 Result、用 task_id 标识（第 10 条）
  - 规则「只有 PM 和 Critic 能派发任务」后面：单只股票的案件只能派这只股票的任务；寻股只能派给 Quant 和 Fundamentals（第 7 条）
  - 规则「PM 每个案件派发的任务数有上限」后面：按请求的顺序逐个检查，没超限的照常派发；被拒绝和复用的不算任务数；一次 `dispatch_tasks` 至少派发出去一个任务才算一轮；寻股的上限和其他案件相同（第 4、5、13 条）
  - 规则「超时返回超时」后面：截止时间以后收到的输出也算超时；超时的任务算任务数（第 6 条）
  - 规则「同一案件内相同的任务去重」后面：任务类型、股票、问题和输入都相同才算，不论发起者；只复用还在运行或者完成、无信号的 Result，复用不算任务数和轮次（第 2、3 条）
  - 任务清单后面：目标由任务类型确定；某日期以来的新闻汇总的起始日期写在 `question` 里（第 8、11 条）
  - 最低要求的"派过任务就算，不看 Result 的状态"后面：被网关拒绝的任务不算（第 9 条）
  - 「PM 如何调用子 agent」：PM 在提交结论以前派任务，回应质询时需要的信息由 Critic 的补充研究提供（第 12 条）

  验证：各处和 proposal 的说法一致；`git diff` 里完整版 Proposal 只有这一节有改动

## 2. contracts：任务清单、任务请求和 Task

- [ ] 2.1 先写测试 `tests/contracts/test_task.py` 的前半部分，每个场景一个测试：
  - 「任务清单」两个场景：八种类型各自的 `agent`；不在清单里的类型解析 `TaskRequest` 时抛 `ValidationError`
  - 「任务请求的格式」三个场景：缺少一项时断言错误的位置就是缺少的那一项
  - Task 的格式部分（design D2）：`target` 是任务类型所属的子 agent；发起者是 `news` 或 `reviewer` 时抛 `ValidationError`（「只有 PM 和 Critic 能派任务」）；截止时间不晚于派发时刻、Critic 的任务带轮次都报错；赋值报错、Task 不变

  验证：运行这个测试文件，确认因为 `contracts/task.py` 不存在而失败
- [ ] 2.2 新增 `contracts/task.py`：`TaskType`（带 `agent`）、`Dispatcher`、`TaskRequest`、`Task`（design D1、D2），模块 docstring 写明 task-dispatch spec。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. contracts：Result

- [ ] 3.1 先在 `tests/contracts/test_task.py` 加 Result 的测试：
  - 「Result 的格式」表的五种状态各构造一个合法的 Result；`cited_entries` 完成和无信号时取自输出，其他状态为空
  - 「状态和内容不符」场景的每一种情况都抛 `ValidationError`；耗时小于 0 也报错
  - 性质测试（Hypothesis，design D12）：任意合法的 Task 和五种状态的 Result 按 JSON 写出再读回，对象相等，原因读回同样的枚举类型

  验证：运行这个测试文件，确认因为 `Result` 等名字不存在而失败
- [ ] 3.2 在 `contracts/task.py` 实现 `ResultStatus`、`TaskRejection`、`TaskFailure`、`Result`（design D3）。验证：3.1 和之前的测试通过；`uv run mypy`、`uv run pytest tests/contracts tests/architecture` 通过

## 4. 配置

- [ ] 4.1 先在 `tests/modules/gateway/test_config.py` 加 spec「派发的配置」的「默认配置」「配置不合法」两个场景；另外检查四项取最小的合法值（1、1、1、1 秒）都合法。验证：运行这个测试文件，确认因为字段不存在而失败
- [ ] 4.2 `GatewayConfig` 加 `max_pm_tasks`、`max_pm_rounds`、`max_critic_tasks`、`task_timeout`（design D9），docstring 写明 task-dispatch spec。验证：4.1 和之前的网关测试通过；`uv run mypy` 通过

## 5. 派发规则

- [ ] 5.1 先写测试：
  - `tests/modules/gateway/builders.py` 补充：spec「任务清单」里的基准数据（价格请求、新闻请求、财报请求；`request(base, **changes)` 只改几项；K-1 到 K-4 的 `CaseScope`，案件 ID 是固定的 UUID）；直接构造 Task 和 Result 的辅助函数，用来拼出 `TaskRecord`
  - `tests/modules/gateway/domain/test_dispatch.py`，直接构造记录：
    - `same_task`：类型、股票、问题、输入各改一项都不相同；输入顺序不同、发起者不同仍相同
    - `reusable`：还没有 Result、完成、无信号的复用；超时、失败、被拒绝的不复用；不相同的不复用
    - `dispatch_rejection`：spec「派发的顺序」表的每一行都有通过和不通过两种情况；寻股不查股票；Critic 不查轮次；任务数和额度的边界（上限减 1 通过、等于上限被拒绝）；被拒绝的任务不计数；多项不通过时给出表里靠前的一项
    - `rounds_used`、`tasks_used`、`usage`、`dispatched_agents`、`completed_results`：spec「本案件的任务和用量」三个场景的数据；被拒绝的任务不算派过，还在运行和无信号的算派过、不算完成；`completed_results` 的键是 `str(task_id)`
    - `new_task`：照抄请求的四项，截止时间是派发时刻加任务时限，轮次按传入的

  验证：运行这个测试文件，确认因为 `domain/dispatch.py` 不存在而失败
- [ ] 5.2 实现 `domain/dispatch.py`（`CaseScope`、`TaskRecord`、`DispatchAction`、`Dispatch`、`TaskUsage` 和 design D4、D8 的函数、`new_task`），并从 `__init__.py` 导出（测试只从包根 import）。验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 6. 输出、调用失败和超时变成 Result

- [ ] 6.1 先写测试 `tests/modules/gateway/domain/test_result.py`，直接构造 Task（派发时刻 15:00Z，截止时间 15:05Z）：
  - `received`：spec「子 agent 的输出变成 Result」表的每一行（合法的信号、合法的无信号、`Malformed`、评估窗口不在选项里、数据截至时刻晚于现在），失败的说明分别是 `Malformed` 的说明和 `SignalRejection` 的值；数据截至时刻等于现在时完成
  - 截止时间的边界：15:05:00Z 收到照常处理，15:05:01Z 收到是超时、没有输出，不论输出合不合法
  - `call_failed`：截止时间以前是失败、原因调用失败、说明原样；以后是超时
  - `expired`：15:04:59Z 抛 `ValueError`；15:05:00Z 和以后是超时，耗时是记超时的时刻减派发时刻
  - `rejected`：耗时和费用都是 0，原因原样
  - 耗时和费用：15:01:30Z 收到时耗时 90 秒，费用原样

  验证：运行这个测试文件，确认因为 `domain/result.py` 不存在而失败
- [ ] 6.2 实现 `domain/result.py`（`Malformed`、`received`、`call_failed`、`expired`、`rejected`，design D5），并从 `__init__.py` 导出。验证：6.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run pytest tests/architecture` 通过

## 7. 存储和用例

- [ ] 7.1 先写测试：
  - `tests/fakes/tasks.py`：`InMemoryTaskStore`（design D6），同一个 Task 第二次写 Result 时抛错
  - `tests/modules/gateway/builders.py` 补充 `task_service(clock=None, store=None, **config)`：用 `FakeClock`（2026-10-14 15:00Z）、`FakeIds`、内存存储和配置新建 `TaskDispatchService`；基准信号、基准无信号的 JSON 文本
  - `tests/modules/gateway/test_service.py`，每个场景一个测试，T-n 就是 `UUID(int=n)`：
    - 「Task 的格式」的「派发出去的 Task」「被拒绝的请求也有 Task」「Critic 的任务没有轮次」
    - 「Result 的格式」的「完成和无信号的 Result」
    - 「派发的顺序」三个场景、「相同的任务」九个场景、「案件的股票」两个场景、「寻股派给谁」三个场景
    - 「PM 的轮次」六个场景、「PM 的任务数」五个场景、「Critic 的补充研究额度」三个场景
    - 「子 agent 的输出变成 Result」四个场景（JSON 文本经 service 解析；「结构错误」断言说明里有 `risks`）、「超时」四个场景、「调用失败」、「一个任务只有一个 Result」三个场景
    - 「本案件的任务和用量」三个场景、「派发的配置」的「改成别的值」

  验证：运行这个测试文件，确认因为 `ports.py`、`service.py` 不存在而失败
- [ ] 7.2 实现（design D6、D7）：
  - `ports.py`：`TaskStore`
  - `service.py`：`TaskDispatchService`（`dispatch`、`receive`、`fail`、`expire`、`record`、`records`）；`receive` 用 `TypeAdapter(SubAgentOutput)` 解析 JSON 文本，`ValidationError` 转成 `Malformed`，说明按 design D7 的格式
  - `__init__.py` 导出 `TaskStore`、`TaskDispatchService`，模块 docstring 加上任务派发和 task-dispatch spec

  验证：7.1 和之前的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 8. 状态机性质测试

- [ ] 8.1 写 `tests/modules/gateway/test_state_machine.py`：Hypothesis 的 `RuleBasedStateMachine`，规则和每一步之后的不变量按 design D12。验证：`HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/gateway/test_state_machine.py` 通过；临时把 `dispatch_rejection` 的任务数比较改成 `>`，确认状态机能找到反例，再改回来

## 9. 整体检查

- [ ] 9.1 对照两份 delta spec（task-dispatch、case-types），确认每个场景都有对应的测试，测试名或说明里写明场景；case-types 新增的「被拒绝的任务不算派过」对应 `dispatched_agents` 不含被拒绝的任务的测试，加上调度器已有的「缺少事件要求的子 agent」测试。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 9.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
