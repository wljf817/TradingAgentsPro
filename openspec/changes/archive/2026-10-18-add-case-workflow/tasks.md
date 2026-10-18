# Tasks

## 1. 前提和人的决定

- [x] 1.1 确认前提：「案件记录」「替身」已经 apply 和归档（`CaseRecordService`、`AgentCallService`、`contracts/agent_call.py` 在 main 上，`openspec/specs/agent-calls/` 存在）；核对 agent-calls、case-stages、program-log、call-budget 的 delta 和当时的主 spec 对得上（MODIFIED 的要求除本变更加的行以外和主 spec 相同），对不上时按主 spec 重写；proposal「需要人决定」已经定了并写进 proposal、spec、design 和完整版 Proposal「共享上下文」。验证：`uv run python -c "from tradingagentspro.modules.caserecords import CaseRecordService; from tradingagentspro.modules.gateway import AgentCallService"` 成功；`openspec validate add-case-workflow --strict` 通过、没有"归档会拒绝"的提示，proposal、spec、design、tasks 的说法一致

## 2. Agent 网关按案件读费用记录

- [x] 2.1 先写测试（design D11）：`tests/fakes/costs.py` 的 `InMemoryCostStore` 加 `case_costs`；`tests/modules/gateway/` 的预算用例测试加 call-budget spec「一个案件的费用记录」的前三个场景（经 `CallBudgetService.record` 记下，经 `case_costs` 读）；repo 测试加 `PostgresCostStore.case_costs` 的往返和"读出错"场景。每个测试的说明写明对应的场景。验证：repo 测试要开着 OrbStack（`open -a OrbStack`）；确认因为 `case_costs` 不存在而失败
- [x] 2.2 实现：`modules/gateway/ports.py` 的 `CostStore.case_costs`，`repo.py` 的 `PostgresCostStore.case_costs`（按 `case_id` 筛、按 `id` 排序），`service.py` 的 `CallBudgetService.case_costs`，`__init__.py` 的说明。验证：2.1 的测试通过；`tests/modules/gateway` 原有的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 案件上下文和程序日志的动作

- [x] 3.1 先写测试（design D7）：`tests/contracts/test_agent_call.py` 加：`CaseContext` 带最近结束的案件（有结论和没有结论的各一个）和待执行的跟进能构造，两项不传时为空；`RecentCase` 缺少结束原因时格式错误。验证：确认因为 `RecentCase` 不存在而失败
- [x] 3.2 实现：`contracts/agent_call.py` 的 `RecentCase` 和 `CaseContext` 的两项（design D7）；`modules/programlog/domain/entry.py` 的 `Action` 加 `OPEN_CASE`、`CASE_STAGE`、`WAKE_AGENT`（常量不写测试）。验证：3.1 的测试通过；`tests/contracts/test_all_models.py`、`tests/integrations/standin` 原有的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 4. 案件状态：不要人工确认时进入等下单

- [x] 4.1 先写测试（design D4）：`tests/modules/scheduler/domain/test_stage.py` 按本变更的 case-stages delta 改：「风控通过」「风控调整」「有分歧的方案照常过风控」传要人工确认；加「不要人工确认的退出方案」「拒绝时不看要不要确认」；其他调用 `risk_checked` 的测试补上 `confirming`。验证：确认因为 `risk_checked` 不接收 `confirming` 而失败
- [x] 4.2 实现：`modules/scheduler/domain/stage.py` 的 `risk_checked(outcome, *, confirming)`（拒绝时结束；通过或调整时按 `confirming` 进入等人工确认或等下单），模块说明里"所有方案都进入等人工确认"改掉。验证：4.1 的测试和 `tests/modules/scheduler` 原有的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 5. 中文名、触发和文字

- [x] 5.1 先写测试（design D12、D5）：新建 `tests/workflows/test_case_entries.py`：`case_trigger` 对四种触发给出的种类、股票、时刻和触发原因（「案件记录」design D8 的表，定时心跳的七种检查项各一例）；退回原因的文字（信号不合法带原因、备用模型的限制、不在粗筛名单里带股票、结论校验的五种、论点日期的十种、跟进审批的七种各给出非空且互不相同的文字）；开案件、案件阶段变化（进入 PM 回应写质询轮次、进入风控写 Critic 审核的结果、结束写结束原因和有没有结论）、唤醒 agent（四种原因、四种交回、调用失败）的说明包含 program-log spec 场景里写明的内容。验证：确认因为 `workflows/case_entries.py` 不存在而失败
- [x] 5.2 实现：`workflows/names.py` 加案件类型、阶段、结束原因、事件类别、决定的中文名；新建 `workflows/case_entries.py`。验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 结论的检查

- [x] 6.1 先写测试（design D5）：新建 `tests/workflows/test_case_conclusion.py`，直接构造结论、`CaseDefinition`（用 `define_case`）、股票池记录、快照和任务记录：
  - 第 1 到 5 项按顺序给出第一项不通过的原因：case-workflow spec「信号不合法退回」「备用模型的限制退回」「研究不足退回以后再交」「寻股只能加名单里的股票」「论点里的日期退回」「亏损的持仓续期」「引用日程项的催化剂」「多项不通过时给出第一项」
  - `thesis_updates` 按 spec「结论的检查」第二张表的五行各一例：新设不传原来的论点；复查类案件是续期、其他是平时调整，原来的论点和上次复查的那一天取股票池记录；浮动盈亏比例用最新价、没有最新价用前一交易日收盘价、没有行情或没有这只持仓时不传；日程项查到财报发布条目时取发布日，查不到或不是财报发布时不传
  - 阈值从配置读取，期望的日期按配置和日历算

  验证：确认因为 `workflows/case_conclusion.py` 不存在而失败
- [x] 6.2 实现：`workflows/case_conclusion.py` 的检查（纯函数，"现在"和今天作为参数）和 `thesis_updates`。验证：6.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 每一步的用例

- [x] 7.1 先写测试（design D1、D7、D9）：新建 `tests/workflows/case_builders.py`（case-workflow spec 的基准数据：股票池、券商账户、行情、K-1 到 K-7 的 `CaseDefinition` 和触发、快照）和 `tests/workflows/test_case_flow.py` 的第一部分，用 `tests/fakes/` 的内存实现、「替身」的 `ScriptedBackend` 和 `FakeClock` 组出 `CaseFlow`：
  - `start`：案件记录的开始运行和开案件的条目（spec「开始一个案件」）
  - `snapshot`：给出版本号；券商、行情、粗筛数据源读不到时为 None，不保存快照（spec「第 1 版读不到」「补充研究的快照读不到」）
  - `context`：spec「案件上下文」的四个场景
  - `call_pm`、`call_critic`、`call_reviewer`：请求的内容（上下文、指定版本的快照、对话；Critic 收到 PM 派过的任务和 Result）；唤醒 agent 的条目（原因、交回、模型、耗时、费用）；全部不可用时告警、写调用失败的条目、给出 None；子 agent 全部不可用时没有告警
  - `dispatch`、`outcomes`：spec「被拒绝和复用的请求」「超时的任务照样交回」

  每个测试的说明写明对应的 spec 场景。验证：确认因为 `workflows/case_flow.py` 不存在而失败
- [x] 7.2 实现：`workflows/case_flow.py` 的 `CaseServices`、`CaseFlowConfig`（design D7）和 `CaseFlow` 的 `start`、`snapshot`、`context`、`call_pm`、`call_critic`、`call_reviewer`、`dispatch`、`run_task`、`outcomes`、`stage`。验证：7.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过
- [x] 7.3 先写测试（design D5、D6、D9、D4）：`tests/workflows/test_case_flow.py` 的第二部分：
  - `conclude`：spec「跟进审批最后检查」「重交次数用完」的检查部分；spec「结论生效」的每一行和「股票池不通过不改变结论」「跟进申请获批」；评分在状态变化以前记下；有方案时补上方案 ID、股票和板块（spec「补上方案 ID、股票和板块」），案件记录已经有方案记录时用它的方案 ID；发现按 case-records spec 写
  - `record_review`、`record_response`、`critic_unavailable`、`record_risk`（交接给出的风控结果写进案件记录）
  - `end`：spec「Task 数和调用次数」「下一个案件从主模型开始」，以及「第 1 版读不到」的 Task 数和调用次数是 0

  验证：确认因为这些方法不存在而失败
- [x] 7.4 实现：`workflows/case_conclusion.py` 的结论生效，`CaseFlow` 的 `conclude`、`record_review`、`record_response`、`critic_unavailable`、`record_risk`、`end`。验证：7.3 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 8. DBOS 工作流

- [x] 8.1 先写测试（design D1、D2、D3、D4、D8）：新建 `tests/fakes/plan_handoff.py` 的 `ScriptedHandoff`（满足 `PlanHandoff`：按脚本给出 `Handoff` 或 None，记下收到的案件 ID、方案和 Critic 审核的结果，不启动工作流）；新建 `tests/workflows/test_case_workflow.py`，真的 DBOS 和 testcontainers 的数据库（照 `test_order_workflow.py` 的 `Harness`），第一部分：spec「开始一个案件」「同一个案件开始两次」「开仓案件的进展」「没有方案的案件」「没有开始的案件」「读同一版快照」「第 1 版读不到」「派发以后提交结论」「任务同时运行」（`ScriptedBackend` 运行 T-1 时等到 T-2 也开始以后才交回）「普通预算用完以后照常运行」、spec「结论的检查」的退回和重交用完、spec「结论生效」里没有方案的结论结束、spec「复盘案件」「Reviewer 不可用」「PM 不可用」。验证：要开着 OrbStack；确认因为 `workflows/case_workflow.py` 不存在而失败
- [x] 8.2 实现：新建 `workflows/plan_handoff.py`（`PlanHandoff`、`Handoff`、`PlanEvent`、`PLAN_TOPIC`、`report_plan`，design D4）；`workflows/case_workflow.py` 的 `run_case`（研究、结论和复盘的部分）、子工作流 `case_task`、各个 step、`install(flow, handoff)`、`start_case`、`case_progress`、`CaseProgress`。验证：8.1 的测试通过；`plan_handoff.py` 不 import `case_workflow.py`；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过
- [x] 8.3 先写测试（design D4、D9）：`tests/workflows/test_case_workflow.py` 的第二部分：spec「Critic 审核」「PM 回应」「交给下单」的全部场景（包括「不要人工确认的退出方案」；`ScriptedHandoff.submit` 在工作流函数里被调用；送回的结果用 `report_plan`；「盘后确认等到开盘」拨测试时钟）、「PM 回应时不可用」「Critic 不可用」、spec「崩溃恢复」的两个场景（照 `test_order_workflow.py` 的 `Crash` 和重启）。验证：确认因为 Critic、回应、交接的部分还没有实现而失败
- [x] 8.4 实现：`run_case` 的 Critic 审核、PM 回应和交给下单的部分（`submit` 在工作流函数里调用，随后的 step `case.record_risk` 写风控结果，`risk_checked(outcome, confirming=)`），`case_workflow.py` 和 `plan_handoff.py` 的模块说明写明给「排队运行」「方案接下单」的接口（design D3、D4）。验证：8.3 和 8.1 的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 9. 收尾

- [x] 9.1 对照本变更的五份 delta spec，逐条列出每个场景由哪个测试覆盖，没有遗漏（「案件工作流的配置」的"默认配置"按测试约定不写测试，对照 `CaseFlowConfig` 核对；program-log spec 的五个新场景由 5.1 的文字测试和 7.1、7.3 里读程序日志的断言覆盖；case-stages spec 改过的两个要求由 4.1 覆盖）。验证：对应表写在完成报告里
- [x] 9.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-case-workflow --strict`。验证：全部通过；`git status` 里没有 `tests/` 下的文件被暂存
