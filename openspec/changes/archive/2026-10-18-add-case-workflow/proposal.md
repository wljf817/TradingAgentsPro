# Proposal

## Why

M3 写好了案件的规则（案件类型和结论的校验、案件状态、任务派发、备用模型、调用预算），M4 前面的变更建好了快照、案件记录和替身，但还没有东西把一个案件从开始跑到结束：没有人唤醒 PM、派任务、校验结论、让结论生效、交给 Critic、跑复盘，案件状态也没有地方存。下一组的「排队运行」要启动案件、看案件走到哪一步，「方案接下单」要把案件的方案接到下单流程，都要先有一个案件的运行流程。

本变更的目的只有一个：**一个案件一个 DBOS 工作流，用配置的 agent（M4 是替身）把开始运行的案件从开始跑到结束，并把案件的进展发给队列。**

引用的 Proposal（完整版）章节：

- 「架构」的「调度器」（为案件生成固定快照并推进阶段：唤醒 PM → 等待结论 → 唤醒 Critic → 交给风控引擎 → 人工确认 → 执行）、「决策层：PM 与子 agent」（PM 最多派 2 轮任务后提交结论，结论的种类，开仓和升级的研究要求）、「审核层：Critic」（通过、质询、补充研究、否决；退出类方案只审 1 轮、不能否决）
- 「调度器与运行模块」：案件流程 1–6；运行约束（每个案件开始时生成快照、agent 调用的超时和重试由 Agent 网关执行）
- 「Task / Result 通信」：PM 和 Critic 经 Agent 网关派发任务、Result 作为工具结果交回，Critic 的补充研究结果同时交给 PM 和 Critic，PM 回应质询时不再派任务
- 「共享上下文」：案件上下文的内容；同一案件读同一版快照，补充研究需要新数据时生成新的快照版本并记录原因
- 「故障处理」：结论通过校验以前因故障结束的案件算没有结论，之后的故障不改变结论；PM、Critic 的模型全部不可用时的处理
- 「股票池与寻股」：PM 的增删和升降级由代码校验；寻股的结论以候选加入股票池

已经定了的（M4 拆分和前面的变更）：

- 什么时候开始一个案件不在本变更：「排队运行」决定并调用本变更的"开始运行一个案件"；三种触发在「触发」组
- 交易方案接到下单流程（风控 → 人工确认 → 再校验 → 下单，确认消息带 Critic 的意见，结束原因交回案件）是下一个变更「方案接下单」（`update-order-flow-case-plans`，已提交）；本变更定交接的接口，下单那一侧由它实现。案件阶段由案件工作流推进，所以不要人工确认的退出方案"风控 → 等下单"的转换在本变更里加
- 紧急退出中止运行中的案件、新案件排队时作废方案是「调度」组的变更；本变更不处理这两个事件，案件状态里它们的转换不变
- agent 只经 Agent 网关的 `AgentCallService` 调用（「替身」）；Task 数和调用次数在案件结束时交给案件记录，调用次数是网关为本案件记下的费用记录条数（「案件记录」design D9）

## What Changes

- 新增案件工作流（`workflows/case_workflow.py`，每一步的用例在 `workflows/case_flow.py` 和 `workflows/case_conclusion.py`），一个案件一个 DBOS 工作流，以案件 ID 为标识：
  - 开始：写案件记录的开始运行，生成第 1 版快照；数据读不到时按系统故障结束
  - 研究：按案件上下文、最新一版快照和 PM 的对话唤醒 PM；PM 派发的任务经网关放行，派发出去的任务并行交给子 agent 运行，Result 按请求的顺序交回 PM
  - 结论的检查，按这个顺序：结论里的信号、备用模型的限制、结论的校验、寻股加入的股票在粗筛名单里、论点里的日期、跟进审批；不通过时带着原因退回 PM 重交，重交用完按结论无效结束
  - 结论生效：写案件记录的结论；记评分、论点更新、升降级和保持、股票池增删、跟进作废；有交易方案时补上方案 ID、股票和板块，进入 Critic 审核
  - Critic 审核：补充研究时生成新的一版快照并记下原因，补充研究的任务以 Critic 为发起者派发；按审核推进案件状态；质询时唤醒 PM 回应，修改的条款代替原来的；写案件记录的审核和回应
  - 交给下单：方案进入风控阶段时在工作流函数里调用"交接"接口，它给出风控结果和要不要人工确认；之后按下单那一侧送回的结果推进到结束
  - 复盘案件：唤醒 Reviewer
  - 结束：写案件记录的结束（Task 数、调用次数），清掉本案件的备用模型起点
  - PM、Critic、Reviewer 的模型全部不可用时告警，按案件状态的规则结束或跳过 Critic
- 案件状态存在 DBOS 工作流里：工作流按步骤的结果推进 `CaseState`，每次变化发布一个"案件的进展"事件（案件类型、股票、案件状态、现在的方案），队列从这里读，不另建表（design D2）
- 给队列的接口：开始运行一个案件、读一个案件的进展；给「方案接下单」的接口：交接端口、交接的结果和送回结果的入口，放在 `workflows/plan_handoff.py`，下单流程不用 import 案件工作流（design D3、D4）
- 案件状态加"风控 → 等下单"：风控通过或调整、不要人工确认（模型退出自动执行）时直接进入等下单（case-stages spec「风控」「人工确认」，`CaseState.risk_checked` 多一个 `confirming`）
- 案件上下文加两项：这只股票最近结束的案件（结论和结束原因，个数可配置，默认 3）和待执行的跟进（`contracts/agent_call.py` 的 `CaseContext`）
- Agent 网关按案件给出费用记录（`CostStore.case_costs`、`CallBudgetService.case_costs`）
- 程序日志加调度器的三个动作：开案件、案件阶段变化（含质询轮次和结束原因）、唤醒 agent
- 不装配：bootstrap 不建案件工作流和它用到的部件（design D10）
- 完整版 Proposal「共享上下文」一句（已改）："补充研究需要新数据时生成新的快照版本并记录原因"改为"Critic 要求补充研究时生成新的快照版本并记录原因"

## Non-goals

- 什么时候开始案件、排队、并发、预算的判断、不再运行的案件：「排队运行」
- 交接的下单那一侧：风控检查、要不要人工确认、人工确认（含确认消息里的双方意见）、再校验和下单、交易记录的来源案件 ID：「方案接下单」，要做的事列在 design D4
- 成交以后股票池的买入成交和清仓、跟进的作废：「方案接下单」的 pool-position-sync（盘中巡检按券商持仓补上）
- 紧急退出中止案件、新案件排队时作废方案：「紧急退出中止案件」「排队运行」
- 跟进案件附上原案件的结论和申请原因：触发里还没有跟进 ID，由「跟进到点」加
- 事件的内容、经验和历史案件的数量索引、查询工具：「事件检测」、M5
- 真实模型、prompt、PM 和 Critic 输出格式不合法时怎样退回：M5
- 复盘的内容、复盘报告存在哪里：M6；本变更只把报告的总结写进程序日志
- 装配到 bootstrap：「排队运行」接上第一个运行中的调用方时一起装配
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

- `case-workflow`：案件工作流：开始运行和案件的进展、第 1 版快照、案件上下文、PM 的研究和派发、结论的检查和退回、结论生效、Critic 审核和补充研究、PM 回应、交给下单、复盘、结束、全部不可用时的告警、崩溃恢复、配置

### Modified Capabilities

- `agent-calls`：修改「每个 agent 的输入和输出」，案件上下文加最近结束的案件和待执行的跟进
- `call-budget`：新增「一个案件的费用记录」：按案件给出费用记录
- `program-log`：修改「程序日志的条目」（对象可以是案件 ID）和「调度器的动作」（加开案件、案件阶段变化、唤醒 agent）
- `case-stages`：修改「风控」（通过或调整、不要人工确认时进入等下单）和「人工确认」（第一句：要确认的方案才进入等人工确认）

## Impact

- **代码**：
  - 新增 `src/tradingagentspro/workflows/case_workflow.py`、`case_flow.py`、`case_conclusion.py`、`case_entries.py`、`plan_handoff.py`
  - `src/tradingagentspro/modules/scheduler/domain/stage.py`：`CaseState.risk_checked(outcome, *, confirming)` 和说明
  - `src/tradingagentspro/workflows/names.py`：案件类型、阶段、结束原因、事件类别的中文名
  - `src/tradingagentspro/contracts/agent_call.py`：`CaseContext` 加两项、新增 `RecentCase`
  - `src/tradingagentspro/modules/gateway/`：`ports.py`、`repo.py`、`service.py` 按案件读费用记录
  - `src/tradingagentspro/modules/programlog/domain/entry.py`：三个动作
  - bootstrap、shared 不改，别的模块只改上面列出的；没有迁移
- **测试**：`tests/modules/scheduler/domain/test_stage.py` 的风控转换；`tests/workflows/test_case_flow.py`、`test_case_conclusion.py`、`test_case_entries.py`、`test_case_workflow.py`（真的 DBOS，要开着 OrbStack）和 `case_builders.py`；`tests/fakes/costs.py`、`tests/fakes/plan_handoff.py`；`tests/modules/gateway/` 的按案件读费用记录
- **依赖**：无
- **文档**：完整版 Proposal「共享上下文」一句（见 What Changes）
- **数据库**：无，没有迁移
- **前提**：「案件记录」「替身」已经 apply 和归档（`CaseRecordService`、`AgentCallService`、`contracts/agent_call.py`）；agent-calls 的 delta 按替身归档后的主 spec 重写
- **对后续变更的影响**：
  - 「方案接下单」：实现交接端口 `PlanHandoff`（在案件工作流的工作流函数里被调用，给出 `Handoff`），下单流程用 `report_plan` 把确认和下单的结果送回案件（design D4 的清单）；它的 tasks 1.1 核对本变更已经归档
  - 「排队运行」：用 `start_case` 启动案件、`case_progress` 组出队列快照里运行中的案件和还没提交订单的方案；不再运行的案件用 `case_trigger` 换出案件记录的触发；装配案件工作流
  - 「紧急退出中止案件」「排队运行」：中止和作废经同一个消息主题送给案件工作流，在步骤之间和等下单时处理（design D3）

## 需要人决定

完整版 Proposal 对应章节的问题，人在 2026-10-18 定了（「按你推荐的：每次补充研究都生成新快照；寻股只能从粗筛名单里挑。」），已经写进 spec、design 和完整版 Proposal：

1. **补充研究什么时候"需要新数据"**：Proposal「共享上下文」原来没有说由谁判断要不要新数据。已定：Critic 每次要求补充研究都生成新的一版快照，原因由代码写明是 Critic 第几次补充研究；补充研究的任务和这以后的 Critic、PM 调用都读新的一版。完整版那一句改为"Critic 要求补充研究时生成新的快照版本并记录原因"
2. **寻股能不能加粗筛名单以外的股票**：已定：不能。寻股的结论加入名单以外的股票时结论校验不通过，退回 PM 重交（spec「结论的检查」第 4 项）；加入的股票的上市信息都取快照里粗筛名单的。理由：Proposal 的寻股流程是"粗筛出前 N 只 → PM 挑选"，名单以外的股票在快照里没有事实

技术上的（lead 已同意，写进 spec 和 design）：

3. **案件状态存在哪里**：存在 DBOS 工作流里，每次变化发布案件的进展事件；DBOS 的系统表和状态库在同一个数据库里，不另建调度器的表（design D2）
4. **交接的接口**：交接端口在工作流函数里调用（DBOS 不允许在 step 里启动工作流），只做风控检查，给出风控结果和要不要人工确认；之后的结果由下单流程用 DBOS 消息送回案件，案件工作流按案件状态的转换推进；接口放在 `workflows/plan_handoff.py`（design D4）
5. **结论生效以后股票池不通过**：升级时关注名额已满、加入流动性不达标这类不通过，结论照样算数，操作不生效，股票池写程序日志、增删写进案件记录的发现，不退回 PM；跟进审批不通过照 follow-ups spec 退回 PM（design D6）
6. **结论里的信号不合法**：按结论校验不通过退回 PM 重交，原因写明哪一项（design D5）；M5 定别的格式错误怎样退回时一起看
7. **生成快照时数据读不到**：案件按系统故障结束，没有结论；不另外告警，券商和行情断线由健康监控告警（design D1）
8. **近期结论取几个**：默认 3 个，可配置（design D7）
9. **不装配**：bootstrap 里还没有 M4 的任何模块（股票池、跟进、网关、快照），现在也没有谁启动案件；「排队运行」接上时一起装配（design D10）
