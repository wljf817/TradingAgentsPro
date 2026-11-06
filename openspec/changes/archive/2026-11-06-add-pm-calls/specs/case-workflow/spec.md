## MODIFIED Requirements

### Requirement: PM 的研究

唤醒 PM 阶段，案件工作流 SHALL 按案件上下文、最新一版快照和 PM 在本案件的对话（agent-calls spec「对话的每一轮」）调用 PM（研究），按 PM 的回复：

| 回复 | 处理 |
| --- | --- |
| 派发任务 | 以 PM 为发起者经 Agent 网关派发（task-dispatch spec「派发的顺序」）；派发出去的任务同时交给目标子 agent 运行，读派发时最新的一版快照（agent-calls spec「子 agent 的任务」）；全部有了 Result 以后，把这次回复和每个请求的 Task 和 Result（按请求的顺序）作为一轮加进对话，再调用 PM |
| 提交结论 | 按「结论的检查」检查 |
| 输出不合法 | 按 case-stages spec「唤醒 PM 和结论」：退回时把这次输出和原因作为一轮加进对话，再调用 PM；重交次数用完时结束 |
| 调用失败 | 见「模型全部不可用」 |

- 被拒绝和复用的请求 SHALL NOT 交给子 agent；超时、失败的任务照样交回 PM，PM 在缺少这些信息的情况下继续
- 调用预算用完 SHALL NOT 影响运行中的案件（call-budget spec「已经开始的案件」）

#### Scenario: 派发以后提交结论

- **GIVEN** K-1 开始运行
- **WHEN** PM 派发价格请求和财报请求；T-1、T-2 完成；PM 提交 K-1 的开仓结论
- **THEN** T-1、T-2 都交给了子 agent；PM 第二次调用时对话有一轮：派发任务，交回 T-1、T-2 和它们完成的 Result
- **AND** 结论按「结论的检查」检查

#### Scenario: 任务同时运行

- **WHEN** K-1 的 PM 派发价格请求和财报请求
- **THEN** T-2 在 T-1 有 Result 以前就开始运行

#### Scenario: 被拒绝和复用的请求

- **GIVEN** K-1 的 PM 已经派发过价格请求（T-1 完成）
- **WHEN** PM 再派发 AAA 的新闻请求和价格请求
- **THEN** AAA 的新闻请求被拒绝（不是案件的股票），价格请求复用 T-1；没有任务交给子 agent
- **AND** PM 下一次调用时对话的第二轮依次交回被拒绝的 Task 和 Result、T-1 和它完成的 Result

#### Scenario: 超时的任务照样交回

- **WHEN** K-3 的 PM 派发新闻请求，T-1 超时；PM 提交持仓的结论：持有
- **THEN** PM 第二次调用时对话交回 T-1 和超时的 Result；结论照常检查，News 算派过任务

#### Scenario: 普通预算用完以后照常运行

- **GIVEN** K-6 开始运行以后，普通预算用完
- **WHEN** K-6 的 PM 派发价格请求，再提交结论
- **THEN** 任务照常派发和运行，PM 照常调用

#### Scenario: 输出不合法以后重新提交

- **GIVEN** K-1 开始运行
- **WHEN** PM 的输出不合法，原因"缺少止损价"；PM 派发价格请求；T-1 完成；PM 提交结论：继续关注
- **THEN** PM 第二次调用时对话有一轮：输出不合法和原因"缺少止损价"
- **AND** 第二次的唤醒 agent 写明第 1 次退回重交；结论照常检查，重交次数 1
- **AND** 输出不合法不生效：不记评分，不写案件记录的结论

#### Scenario: 输出不合法用完重交次数

- **WHEN** K-1 的 PM 连续 3 次输出不合法
- **THEN** PM 调用了 3 次，结束：结论无效，没有结论

### Requirement: PM 回应

PM 回应阶段，案件工作流 SHALL 调用 PM（回应），对话的最后一轮是 PM 上一次的回复（提交的结论，或者上一轮的回应）和交回的质询：这次审核，以及这次审核里补充研究的 Task 和 Result（agent-calls spec「对话的每一轮」）。按回应（case-stages spec「PM 回应」）：

| 回应 | 处理 |
| --- | --- |
| 维持 | 回到 Critic 审核；Critic 的对话加一轮：这次质询和 PM 的回应 |
| 修改 | 修改后的条款代替现在的方案的条款，方案 ID 和动作不变；回到 Critic 审核，Critic 的对话同上 |
| 撤回 | 结束：PM 撤回 |
| 输出不合法 | 按 case-stages spec「PM 回应」：退回时把这次输出和原因作为一轮加进对话，再调用 PM（回应）；重交次数用完时结束 |
| 调用失败 | 见「模型全部不可用」 |

每次回应 SHALL 写案件记录的回应和 PM 用的模型；输出不合法不是回应，SHALL NOT 写，也 SHALL NOT 改方案。

#### Scenario: 维持以后再审

- **GIVEN** K-1 的方案 P-1 在 Critic 审核
- **WHEN** Critic 派发新闻请求（T-3 完成）以后质询"止损太近"；PM 维持；Critic 通过
- **THEN** PM 回应时对话的最后一轮是：提交的结论，交回这次质询和 T-3 的 Task 和 Result
- **AND** Critic 第三次调用时对话依次有：补充研究和 T-3；质询和维持的回应
- **AND** K-1 进入风控，质询轮次 1

#### Scenario: 修改条款

- **GIVEN** K-1 的方案 P-1 被质询一次，在 PM 回应
- **WHEN** PM 修改：60 股、限价单、限价 50、止损价 46、紧急阈值 0.08；Critic 通过
- **THEN** 现在的方案是 P-1：买入 BBB 60 股，止损价 46；进展和交给下单那一侧的都是它

#### Scenario: 撤回

- **GIVEN** K-1 的方案 P-1 被质询一次，在 PM 回应
- **WHEN** PM 撤回
- **THEN** 结束：PM 撤回，有结论

#### Scenario: 两轮以后仍有分歧

- **GIVEN** K-1 的方案 P-1 在 Critic 审核
- **WHEN** 依次：Critic 质询、PM 维持、Critic 质询、PM 修改、Critic 质询
- **THEN** PM 第二次回应时对话的最后一轮是：第一次的回应，交回第二次质询
- **AND** 进入风控，Critic 审核的结果是有分歧；案件记录有三次审核和两次回应

#### Scenario: 回应的输出不合法以后重新回应

- **GIVEN** K-1 的方案 P-1 被质询一次，在 PM 回应
- **WHEN** PM 的输出不合法，原因"没有逐条回应"；PM 维持；Critic 通过
- **THEN** PM 第二次回应时对话的最后一轮是：输出不合法和原因"没有逐条回应"
- **AND** Critic 的对话里只有质询和维持的回应；案件记录有一次回应

#### Scenario: 回应的输出不合法用完重交次数

- **GIVEN** K-1 的方案 P-1 被质询一次，在 PM 回应，重交次数 2
- **WHEN** PM 的输出不合法
- **THEN** 结束：系统故障，有结论；方案不交给风控
