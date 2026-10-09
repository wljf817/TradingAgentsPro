# Tasks

## 1. 完整版 Proposal 补上新发现的条目

- [x] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 7、9、10 条修改完整版 Proposal，每处只加或改一两句话（第 8 条是默认值，不写进 Proposal）：
  - Agent 表下面：第一版每个 agent 一套主模型和备用模型，表里 Critic、Reviewer 按用途的写法是选模型的建议（第 10 条）
  - 「备用模型」的"什么算不可用"：额度或预算用完改为厂商的额度或余额用完，本系统的调用预算用完不算（第 4 条）
  - "切换顺序"：冷却中的模型直接跳过；子 agent 的任务到了截止时间就停止尝试、记为超时，不告警（第 3 条）
  - "案件内不来回切换"：以调用成功用的模型为准，只往后换、不往回换（第 7 条）
  - "熔断"：数的是重试用完仍不可用的次数，按模型计、不分 agent；冷却结束以后第一次调用仍不可用就再冷却；状态放在内存里，进程重启以后从头算（第 5、6 条）
  - "厂商校验"：厂商指模型出自哪家公司，不是调用的平台（第 9 条）
  - "可选限制"：改为列出 PM 用备用模型时不能提的决定（开仓方案、加仓、升级为关注），其他决定不受限，默认打开；被限制时按结论校验不通过退回 PM 重交（第 1、2 条）
  - 「调度器与运行模块」运行约束的超时和重试一句：子 agent 的超时和重试要让主模型和第一个备用模型都放得进任务时限（第 3 条）

  验证：各处和 proposal 的说法一致；`git diff` 里完整版 Proposal 只有「Agent」和「调度器与运行模块」两节有改动

## 2. contracts：agent 和模型的身份

- [x] 2.1 先写测试 `tests/contracts/test_model.py`（design D1）：`ModelRef` 的厂商去掉首尾空白、转成小写，模型名去掉首尾空白、大小写不变；厂商或模型名为空时抛 `ValidationError`；厂商和模型名都相同的两个 `ModelRef` 相等、哈希相同，可以做 dict 的键；`str()` 是"厂商/模型名"；`ModelUse` 构造以后不能赋值。`Agent` 有六个值，三个子 agent 的值和 `SubAgent` 相同（`Agent(SubAgent.NEWS)` 是 `Agent.NEWS`）。验证：运行这个测试文件，确认因为名字不存在而失败
- [x] 2.2 `contracts/agent.py` 加 `Agent`；新增 `contracts/model.py`（`ModelRef`、`ModelUse`），模块 docstring 写明 model-fallback spec。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. Result 记录实际使用的模型

- [x] 3.1 先写测试：
  - `tests/contracts/test_task.py`：task-dispatch delta「Result 的格式」的「完成和无信号的 Result」（带模型）和「模型和状态不符」；原有的 JSON 往返性质测试让完成、无信号、格式不合法的失败带上随机的 `ModelUse`
  - `tests/modules/gateway/builders.py`：spec 的基准模型 s-1、s-2 和对应的 `ModelUse`；`result()` 给完成、无信号、格式不合法的失败默认带 s-1 的 `ModelUse`
  - `tests/modules/gateway/domain/test_result.py`：`received` 截止时间以前收到时完成、无信号、格式不合法的失败都带传入的 `ModelUse`，晚于截止时间是超时、不带；`call_failed`、`expired`、`rejected` 都不带
  - `tests/modules/gateway/test_service.py`、`test_state_machine.py`：已有的 `receive` 调用都多传一个 `ModelUse`；补 delta「子 agent 的输出变成 Result」的「收到合法的信号和无信号」「备用模型给出的输出」「结构错误」「评估窗口不在配置的选项里」「晚于截止时间收到的不记模型」，「调用失败」的两个场景

  验证：运行这几个测试文件，确认因为 `model_use` 不存在而失败
- [x] 3.2 实现（design D8）：`contracts/task.py` 的 `Result` 加 `model_use` 和检查；`domain/result.py` 的 `received` 多收 `model_use`，传给完成、无信号和格式不合法的失败；`TaskDispatchService.receive` 多收 `model_use` 原样传下去；`contracts/task.py` 的 `TaskFailure.CALL_FAILED` 注释改为指向 model-fallback spec 的全部不可用，`domain/result.py` 的 docstring 写明完成、无信号和格式不合法的失败记下给出输出的模型。验证：3.1 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check` 通过

## 4. 配置

- [x] 4.1 先写测试：
  - `tests/modules/gateway/builders.py` 补充 spec「模型的配置」的基准模型的配置：p-1 到 p-3、c-1、c-2、s-1、s-2，`assignments(**changes)` 只改几个 agent 的模型
  - `tests/modules/gateway/test_config.py`：「模型的配置」三个场景、「厂商校验」两个场景；「备用模型的配置」的「默认配置」「配置不合法」「放得进任务时限的边界」；已有的"派发的四项取最小的合法值"测试改为同时把重试次数配置为 0、子 agent 的单次调用超时配置为 0.5 秒，才放得进 1 秒的任务时限

  验证：运行这个测试文件，确认因为名字和字段不存在而失败
- [x] 4.2 `config.py` 加 `AgentModels`、`ModelAssignments` 和 `GatewayConfig` 的七个配置项、交叉检查（design D2、D10），改 `GatewayConfig` 的 docstring；从 `__init__.py` 导出 `AgentModels`、`ModelAssignments`。验证：4.1 和全部已有的网关测试通过；`uv run mypy`、`uv run pytest tests/architecture` 通过

## 5. 熔断

- [x] 5.1 先写测试 `tests/modules/gateway/domain/test_fallback.py` 的熔断部分，直接构造 `Breaker`：spec「熔断」状态表的每一行；连续不可用次数减 1 不冷却、等于时冷却；时间窗口等于时冷却、多 1 秒不冷却，最近连续的 N 次滑动；冷却结束的时刻前 1 秒在冷却中、等于时不在；成功在三种状态下都回到正常；冷却结束以后不可用直接再冷却；`failures` 最多留 N 个；「备用模型的配置」的「改成别的值」里熔断的三组。验证：运行这个测试文件，确认因为 `Breaker` 不存在而失败
- [x] 5.2 实现 `domain/fallback.py` 的 `Breaker`（design D4），并从 `__init__.py` 导出（测试只从包根 import）。验证：5.1 的测试通过；`uv run mypy`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 6. 一次调用的步骤

- [x] 6.1 先在 `tests/modules/gateway/domain/test_fallback.py` 写测试，直接构造 `ModelCall` 和熔断状态：
  - `start`：从起点开始；起点在冷却中时跳过、给出切换的事件；全部在冷却中时是全部不可用，说明三个冷却中；到了截止时间是停止
  - `after_failure`：还能重试时重试同一个模型、尝试次数加 1；重试次数为 0 时第一次失败就换；尝试用完时换下一个，切换的事件的原因是这次的调用错误；跳过冷却中的、每越过一个一个事件；最后一个不可用时是全部不可用、没有切换的事件；到了截止时间时不论还能不能重试都停止；七种调用错误都一样处理
  - `attempt_timeout`：子 agent 是 60 秒和离截止时间还剩的时间里较短的，PM、Critic、Reviewer 是 180 秒、不看截止时间
  - `exhausted`：第 1 次尝试在重试次数 1 时为假、第 2 次为真
  - `AllUnavailable.detail`：按先后列出，原因的写法和 spec 的例子一致

  验证：运行这个测试文件，确认因为这些名字不存在而失败
- [x] 6.2 实现 `domain/fallback.py` 的 `CallError`、`Unavailable`、`ModelSwitch`、`ModelCall`、`Attempt`、`AllUnavailable`、`DeadlineReached`、`CallStep` 和 `start`、`exhausted`、`after_failure`、`attempt_timeout`（design D3、D5），并从 `__init__.py` 导出。验证：6.1 和之前的测试通过；`uv run mypy`、`uv run ruff check`、`uv run pytest tests/architecture` 通过

## 7. 备用模型的限制

- [x] 7.1 先写测试：
  - `tests/modules/gateway/domain/test_fallback.py`：`blocked_decision` 覆盖 spec「备用模型的限制」的「用备用模型时被限制的决定」「用备用模型时其他决定都通过」「用主模型时不限制」「不限制或者只限制一部分」，结论用 builders 里 case-types 的基准结论
  - `tests/modules/scheduler/domain/test_stage.py`：case-stages delta 新增的「备用模型的限制也退回 PM 重交」（事件就是已有的结论校验不通过，调度器的代码不改）

  验证：运行网关的测试文件，确认因为 `blocked_decision` 不存在而失败；调度器的新测试直接通过
- [x] 7.2 实现 `domain/fallback.py` 的 `blocked_decision`（design D9），并从 `__init__.py` 导出。验证：7.1 的测试通过；`uv run mypy` 通过

## 8. 用例

- [x] 8.1 先写测试：
  - `tests/modules/gateway/builders.py` 补充 `fallback_service(clock=None, models=None, **config)`：测试时钟（没有传入时停在 15:00Z）、基准模型的配置、默认配置；辅助函数"某模型不可用（调用错误）"连续报告两次失败
  - `tests/modules/gateway/test_fallback_service.py`，每个场景一个测试，K-1、K-2 是固定的 UUID：
    - 「什么算不可用」四个场景（「格式不合法不算不可用」同时用 `TaskDispatchService` 检查 T-1 的 Result 带 s-1）
    - 「尝试的超时」五个场景、「切换顺序」七个场景、「案件内不切回」七个场景
    - 「熔断」十三个场景（「进程重启以后从头算」用新建一个 service 表示重启）
    - 「还有可用的模型」四个场景、「实际使用的模型」两个场景、「切换的事件」四个场景
    - 「备用模型的限制」的「被限制以后退回 PM 重交」：限制给出开仓方案，接着这个案件的 PM 从 p-2 开始（案件状态的部分在 7.1 的调度器测试）
    - 「备用模型的配置」的「改成别的值」里 PM 的单次调用超时一组

  验证：运行这个测试文件，确认因为 `ModelFallbackService` 不存在而失败
- [x] 8.2 实现 `service.py` 的 `ModelFallbackService`（`begin`、`fail`、`succeed`、`case_model`、`end_case`、`has_available_model`、`cooling`，design D6、D7）；`__init__.py` 导出它和 design D12 的其余名字，模块 docstring 加上备用模型和 model-fallback spec。验证：8.1 和之前的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 9. 状态机性质测试

- [x] 9.1 写 `tests/modules/gateway/test_fallback_state_machine.py`：Hypothesis 的 `RuleBasedStateMachine`，规则、参考模型和每一步之后的不变量按 design D13。验证：`HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/gateway/test_fallback_state_machine.py` 通过；临时把 `succeed` 的起点改成直接等于 `call.index`、把 `Breaker.failed` 的窗口比较改成 `<`，分别确认状态机能找到反例，再改回来

## 10. 整体检查

- [x] 10.1 对照三份 delta spec（model-fallback、task-dispatch、case-stages），确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 10.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
