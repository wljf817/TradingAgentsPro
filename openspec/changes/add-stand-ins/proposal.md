# Proposal

## Why

实现路线的原则是"模型最后接：案件流程先用按脚本给结论的替身跑通"。现在代码里没有"调用一个 agent"这件事：Agent 网关会放行任务、把子 agent 的输出记成 Result、给出备用模型的下一步、记费用，但没有调用模型的端口，也没有谁按备用模型的步骤去调用。下一个变更「案件工作流」要唤醒 PM、派任务、交给 Critic、跑复盘，必须先有这个接口和一份能在模拟盘里跑的实现。

本变更的目的只有一个：**在 Agent 网关定义调用 agent 的接口（端口和用例），并提供按脚本给出固定结论的 PM、Critic、三个子 agent 和 Reviewer 的替身；M5 用真实模型实现同一个端口替换它们。**

引用的 Proposal（完整版）章节：

- 「调度器与运行模块」：Agent 网关调用模型（主模型不可用时切换备用模型）、记下每次模型调用的费用、校验输出格式；案件流程的唤醒 PM → PM 派发任务 → PM 出结论（校验不通过时重交）→ Critic 质询或补充研究 → PM 逐条回应；运行约束里每个 agent 调用的超时和重试由 Agent 网关执行
- 「Task / Result 通信」：PM 用 `dispatch_tasks` 和 `submit_conclusion` 两个工具；Agent 网关拦截请求、唤醒子 agent，把 Result 作为工具结果交回 PM；Critic 的补充研究结果同样作为工具结果交给 PM；PM 在提交结论以前派任务，回应质询时需要的新信息由 Critic 的补充研究提供；子 agent 只看到任务内容和共享事实
- 「Agent」：六个 agent 的输入和输出（Critic 输出通过、质询、补充研究请求、否决；Reviewer 输出复盘报告）；备用模型（切换顺序、案件内不切回、厂商校验）；信号统一格式；PM 的交易方案
- 「审核层：Critic」：通过、质询、经 Agent 网关要求补充研究、否决开仓方案
- 「股票池与寻股」：寻股流程（代码粗筛出前 N 只 → PM 挑选 → 以候选加入）；升级为关注时附关注论点

已经定了的（M4 拆分时）：

- 替身怎样给结论由我们定，不是要和人讨论的设计：它们是 M5 换掉的测试工具。每种案件类型一个固定的结论，参数都在配置里
- 替身在模拟盘里运行，是正式代码，放 `integrations/standin/`，不放 `tests/fakes/`
- 替身的输出只由请求决定（同样的快照、案件和对话给出同样的输出），测试和 DBOS 重放都稳定
- 调用方是下一个变更「案件工作流」；没有运行中的调用方，本变更不接 bootstrap

## What Changes

- 新增 `contracts/agent_call.py`：调用 agent 的输入和输出
  - PM：输入是案件上下文（案件要回答的问题、事件类别、事件要求的子 agent、这只股票现在的论点）、快照和 PM 在本案件的对话；输出是派发任务或提交结论，回应质询时输出对质询的回应
  - Critic：输入是案件上下文、快照、要审核的结论、PM 派过的任务和 Result、Critic 的对话；输出是补充研究（派发任务）或审核
  - 子 agent：输入是 Task 和快照；输出是信号或无信号的 JSON 文本
  - Reviewer：输入是快照；输出是复盘报告（只有一段总结，内容在 M6 定）
  - 对话的每一轮是 agent 的回复和交回的结果：派发任务交回每个请求的 Task 和 Result；结论交回校验不通过的原因，或者 Critic 的质询和补充研究的结果；Critic 的质询交回 PM 的回应
- Agent 网关加调用模型的端口 `ModelBackend`：向一个模型发一次请求（一次尝试），带这次的超时；拿不到输出时抛 `ModelCallError`（调用错误，厂商给了用量时带上用量）
- Agent 网关加用例 `AgentCallService`：按备用模型的步骤逐次尝试，每次拿到用量就记一条费用；PM、Critic、Reviewer 的调用给出输出、实际使用的模型和费用，或者全部不可用；子 agent 的任务直接生成 Result（完成、无信号、格式不合法、调用失败或超时），已经有 Result 的任务不再调用
- `ModelFallbackService` 加一把锁：几个案件同时调用时，熔断状态和起点不乱
- 新增 `integrations/standin/`：
  - `StandInBackend` 实现 `ModelBackend`，不调用模型，用量都是 0
  - PM 替身每种案件类型一个固定的结论：关注股的入场条件满足 → 开仓方案；持仓复查 → 持有，复查日往后 5 个交易日；持仓催化剂复查 → 清仓并移出；候选评估 → 升级为关注；寻股 → 加入粗筛的前 2 名；其余 → 不操作（复查类案件按规则附续期的论点）
  - PM 替身先派任务，满足事件要求的子 agent 和开仓、升级的研究要求；回应质询时维持
  - Critic 替身一律通过；子 agent 替身给出固定的信号，引用 Task 的输入；Reviewer 替身给出固定的复盘报告
  - 方案的股数、限价、止损价、紧急阈值和论点的日期由快照的行情、权益和配置算出，通过已有的结论、方案和日期校验
  - `stand_in_models()` 给出替身的模型配置：每个 agent 一个模型，Critic 和 PM 不同厂商，单价 0
- 完整版 Proposal 不改

## Non-goals

- 装配和切换：bootstrap 不建 `AgentCallService` 和替身，也不加"用替身还是真实模型"的配置；「案件工作流」接上时装配替身
- 案件流程本身：什么时候调用谁、结论的校验和退回、Critic 和 PM 回应的轮次、把对话存进 DBOS：「案件工作流」
- 案件上下文的其余部分（近期结论、待执行的跟进、经验和历史案件的索引、事件内容）：由建出来源的变更加进 `CaseContext`
- 真实模型：PydanticAI 的适配器、prompt、把快照和对话转成消息、查询工具：M5
- PM、Critic、Reviewer 的输出格式不合法时怎样退回：M5（signal-format spec「结论里的信号」已经这样定）；替身的输出总是合法的
- 程序日志里的模型调用（完整输入和输出、重试、切换备用模型及原因）：M5 和 prompt 一起加（design D5）；替身不会失败，也不会切换
- 全部不可用时的告警：调用方「案件工作流」做（model-fallback spec「切换顺序」）
- Reviewer 的复盘内容和经验库：M6
- 替身给出有价值的判断：信号、方案和论点都是固定的，M6 的评估按模型的厂商去掉替身
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

- `agent-calls`：调用 agent：每个 agent 的输入和输出、对话的每一轮、一次调用按备用模型的步骤尝试和记费用、子 agent 的任务生成 Result
- `stand-ins`：替身：替身的模型、PM 替身的派发和每种案件类型的结论、方案和论点的算法、续期、寻股、信号、回应质询，Critic、子 agent 和 Reviewer 的替身，替身的配置

### Modified Capabilities

无。model-fallback、call-budget、task-dispatch 的规则不变：`AgentCallService` 就是这几份 spec 里的"调用方"。

## Impact

- **代码**：
  - 新增 `src/tradingagentspro/contracts/agent_call.py`
  - `src/tradingagentspro/modules/gateway/ports.py`：`ModelBackend`、`ModelReply`、`ModelCallError`
  - `src/tradingagentspro/modules/gateway/service.py`：`AgentCallService`、`Answered`；`ModelFallbackService` 加锁
  - `src/tradingagentspro/modules/gateway/__init__.py`：说明和导出
  - 新增 `src/tradingagentspro/integrations/standin/`：`__init__.py`、`config.py`、`backend.py`、`conclusions.py`
- **测试**：`tests/contracts/test_agent_call.py`、`tests/fakes/model_backend.py`（按脚本给出每次尝试的结果）、`tests/modules/gateway/test_agent_calls.py`、`tests/integrations/standin/`
- **依赖**：无
- **文档**：无
- **数据库**：无，没有迁移
- **前提**：「状态库快照」已经 apply（`contracts/snapshot.py`）
- **对后续变更的影响**：
  - 「案件工作流」：装配 `StandInBackend`、`stand_in_models()` 和 `AgentCallService`；每次调用放进一个 DBOS step，step 的输出是回复，崩溃恢复时从 step 的输出重建对话；用 `CaseDefinition` 和股票池记录组出 `CaseContext`；把派发的任务交给 `run_task`（可以并行），等它们的 Result 组成交回的结果；按 case-types、model-fallback、signal-format、review-dates 的顺序校验结论；全部不可用时告警、按系统故障结束；案件结束时调用 `ModelFallbackService.end_case`
  - M5：写 PydanticAI 的适配器实现 `ModelBackend`，配置真实模型；加模型调用的程序日志

## 需要人决定

完整版 Proposal 对应章节的问题：无。本变更没有改变案件流程和交易规则；替身怎样给结论按 M4 拆分时定的做（What Changes）。

技术上的，2026-10-18 都已经定了，做法已经写进 spec 和 design：

1. **端口是"一次尝试"**：`ModelBackend` 只向给定的模型发一次请求；重试、切换备用模型、熔断、记费用都在网关的 `AgentCallService` 里，M3 写好的备用模型和预算规则不用动，M5 的适配器只管调用一个模型（design D1）
2. **输出的格式在哪里校验**：子 agent 交回原样文本，由网关按 task-dispatch spec 记成 Result（格式不合法记失败、不重新调用）；PM、Critic、Reviewer 交回已经按 contracts 校验过的模型，M5 的 PydanticAI 适配器在工具参数上校验（design D4）
3. **PM 回应质询时只能回应**：Proposal 说回应质询时要的新信息由 Critic 的补充研究提供，所以回应时不给 PM 派任务的工具（design D3）
4. **替身的模型名**：`standin/pm`、`standin/news`、`standin/quant`、`standin/fundamentals`、`standin/reviewer`，Critic 是 `standin-critic/critic`，都没有备用模型，单价 0；每次调用照样记一条 0 元的费用，案件记录的调用次数能数出来（design D7）
5. **持仓催化剂复查的清仓移出股票池**，不转为关注；催化剂截止日期默认 8 个交易日、复查日 5 个交易日，持仓复查和催化剂复查不会落在同一天（design D9）
6. **模型调用的程序日志放到 M5**：Proposal 要记每次调用的完整输入（prompt）和输出，prompt 是 M5 写的；替身不会失败、不会切换（design D5）
