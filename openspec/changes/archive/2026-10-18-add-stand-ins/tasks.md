# Tasks

## 1. 前提

- [x] 1.1 确认前提：「状态库快照」已经 apply，`src/tradingagentspro/contracts/snapshot.py` 有 `Snapshot`、`QuoteEntry`、`ScreenEntry`、`entry_id`；按人和 lead 对 proposal「需要人决定」的答复改 proposal（写成已定）和相关的 spec、design。验证：`uv run python -c "from tradingagentspro.contracts.snapshot import Snapshot"` 成功；`openspec validate add-stand-ins --strict` 通过，proposal、spec、design、tasks 的说法一致

## 2. 调用 agent 的输入和输出（contracts）

- [x] 2.1 先写测试 `tests/contracts/test_agent_call.py`（design D13）：agent-calls spec「每个 agent 的输入和输出」的复盘报告为空、问题为空；「对话的每一轮」的五行对应各一个合法的轮次，和"回复和结果不对应"场景的五种报错（另加 `Challenge` 的审核不是质询时报错）；每个测试的说明写明对应的 spec 场景。验证：确认因为 `tradingagentspro.contracts.agent_call` 不存在而失败
- [x] 2.2 实现 `src/tradingagentspro/contracts/agent_call.py`（design D2、D3），`PmTurn`、`CriticTurn` 的 validator 按 spec 的表检查。验证：2.1 的测试通过；`tests/contracts/test_all_models.py` 通过（新模型都是 frozen、extra="forbid"）；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. Agent 网关：端口和调用

- [x] 3.1 在 `src/tradingagentspro/modules/gateway/ports.py` 加 `ModelCallError`、`ModelReply`、`ModelBackend`（design D1），`__init__.py` 导出它们并在说明里写上调用 agent；写 `tests/fakes/model_backend.py` 的 `ScriptedBackend`（按调用的先后给出每次尝试的 `ModelReply` 或 `ModelCallError`，尝试时可以推进 `FakeClock`，记下每次收到的模型、超时和请求）。验证：`uv run mypy` 通过，`ScriptedBackend` 满足 `ModelBackend`（mypy 按协议检查一次赋值）
- [x] 3.2 先写测试 `tests/modules/gateway/test_agent_calls.py`：agent-calls spec「一次调用」的七个场景和「子 agent 的任务」的七个场景，用真的 `ModelFallbackService`、`CallBudgetService`、`TaskDispatchService` 加 `InMemoryCostStore`、`InMemoryTaskStore`、`InMemoryProgramLogStore`、`FakeClock`、`ScriptedBackend`；"几个案件同时调用"用两个线程和 `threading.Barrier` 让两次调用的尝试交错；断言费用记录的条数和金额、Result 的状态、模型和费用、发给模型的请求和超时；阈值从配置读取。验证：确认因为 `AgentCallService` 不存在而失败
- [x] 3.3 实现 `src/tradingagentspro/modules/gateway/service.py` 的 `Answered`、`AgentCallService`（design D5），`ModelFallbackService` 加锁、改类注释（design D6），`__init__.py` 导出。验证：3.2 的测试通过；`tests/modules/gateway/` 原有的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（网关只多依赖 `contracts.agent_call` 和 `contracts.snapshot`）

## 4. 替身的模型、配置、Critic、子 agent 和 Reviewer

- [x] 4.1 先写测试：
  - `tests/integrations/standin/snapshots.py`：stand-ins spec 的基准数据，按条目直接构造各案件的第 1 版快照和 `CaseContext`；函数可以去掉某只股票的行情条目、去掉最新价、换粗筛名单、换论点的日期
  - `tests/integrations/standin/test_config.py`：stand-ins spec「替身的模型」两个场景（第二个经 `AgentCallService.run_task`）、「替身的配置」的"配置不合法"
  - `tests/integrations/standin/test_backend.py` 的一部分：「Critic 替身」「子 agent 替身」（经 `AgentCallService.run_task`）「Reviewer 替身」「PM 替身回应质询」的场景
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.integrations.standin` 不存在而失败
- [x] 4.2 实现 `src/tradingagentspro/integrations/standin/`：`config.py`（`StandInConfig`、`stand_in_models`，design D7、D11）、`backend.py` 的 `StandInBackend`（这一步先做 `pm_respond`、`critic`、`sub_agent`、`reviewer`，`pm` 在第 5 组；design D10）、`__init__.py` 的说明和导出。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（standin 只依赖网关的包根、contracts 和 shared）

## 5. PM 替身

- [x] 5.1 先写测试 `tests/integrations/standin/test_backend.py` 的其余部分：stand-ins spec「替身」的两个场景（同样的请求输出相同；经网关调用一次 PM 替身，记下一条 0 元的费用）、「PM 替身派发任务」「PM 替身的结论」「参考价」「开仓方案」「升级」「续期」「寻股」「PM 替身的信号」的全部场景，「替身的配置」的"改配置改变输出"；阈值从配置读取、期望的价格和日期按配置算，不在测试里硬编码（spec 场景里的数是默认配置下的结果，用来核对算法）。验证：确认因为 `StandInBackend.pm` 不存在而失败
- [x] 5.2 实现 `src/tradingagentspro/integrations/standin/conclusions.py`（`researched`、`conclusion`、`to_tick`、`buy_plan`、`new_catalyst` 和续期、升级、寻股、信号，design D8、D9）和 `StandInBackend.pm`。验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check` 通过
- [x] 5.3 先写测试再修实现 `tests/integrations/standin/test_flow.py`：stand-ins spec「替身的结论通过已有的校验」的两个场景。每种案件类型经 `AgentCallService` 跑 PM 替身直到提交结论（派发经 `TaskDispatchService.dispatch`，任务经 `run_task` 交给子 agent 替身，交回的结果组成对话），再检查：网关的 `conclusion_signal_rejection` 和 `blocked_decision`、调度器的 `conclusion_rejection`（`CaseDefinition` 用 `define_case` 从触发得到，派过任务的子 agent 和完成的 Result 用 `dispatched_agents`、`completed_results`）、心跳的 `thesis_dates_rejection`（开仓和升级是新设，复查类是续期，亏损场景传浮动盈亏比例 -0.10）、结论的 `trade_plan` 补上方案 ID、股票和板块。验证：测试通过；不通过时改替身，不改校验规则

## 6. 收尾

- [x] 6.1 对照两份 spec，逐条列出每个场景由哪个测试覆盖，没有遗漏（「替身的配置」的"默认配置"按测试约定不写测试，对照 `StandInConfig` 核对）。验证：对应表写在完成报告里
- [x] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-stand-ins --strict`。验证：全部通过；`git status` 里没有 `tests/` 下的文件被暂存
