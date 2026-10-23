# Spec Delta

## MODIFIED Requirements

### Requirement: 每个 agent 的输入和输出

Agent 网关 SHALL 按下表把输入交给 agent，收下它的输出：

| agent | 输入 | 输出 |
| --- | --- | --- |
| PM（研究） | 案件上下文、本案件现在的快照、PM 在本案件的对话 | 派发任务：一个或多个任务请求（task-dispatch spec「任务请求的格式」）；或者提交结论：case-types spec 的四种结论之一 |
| PM（回应） | 同上，对话的最后一轮交回的是 Critic 的质询 | 对这轮质询的回应（case-stages spec「Critic 审核和 PM 回应的格式」） |
| Critic | 案件上下文、快照、要审核的结论（带交易方案的持仓或关注的结论）、PM 在本案件派过的任务和 Result、Critic 在本案件的对话 | 补充研究：一个或多个任务请求；或者一次审核：通过、质询或否决（case-stages spec「Critic 审核和 PM 回应的格式」） |
| News、Quant、Fundamentals | Task 和快照 | 输出文本：按 task-dispatch spec「子 agent 的输出变成 Result」校验 |
| Reviewer | 快照 | 复盘报告：一段总结 |

案件上下文是快照以外 PM 和 Critic 要看的、由调用方给出的内容；案件 ID、案件类型和股票在快照里（state-snapshot spec「每种案件的快照」）：

| 项 | 内容 |
| --- | --- |
| 问题 | 案件要回答的问题（case-types spec「案件类型」）；跟进案件是申请时写下的问题 |
| 事件 | 事件案件的事件，整个事件（event-rules spec「事件的内容」），其中的类别就是 case-types spec「触发」的事件类别；其他案件没有 |
| 事件要求的子 agent | 事件案件至少要派给的子 agent（case-types spec「事件要求的子 agent」）；其他案件没有 |
| 关注论点 | 关注股现在的关注论点；其他状态没有 |
| 投资论点 | 持仓现在的投资论点；其他状态没有 |
| 最近结束的案件 | 这只股票最近结束的几个案件，按结束时刻从晚到早：案件类型、结束时刻、结束原因（case-stages spec「结束原因」）和通过校验的结论，没有结论的不写结论；不针对单只股票的案件没有 |
| 待执行的跟进 | 这只股票待执行的跟进的申请（case-types spec「跟进申请的格式」），按触发时间排列；没有时为空 |

- PM 回应质询时 SHALL NOT 派发任务：Proposal 规定回应需要的新信息由 Critic 的补充研究提供
- 子 agent 只看到 Task 和快照，看不到案件上下文、别的任务和 Result
- 任务请求、结论、回应、审核的格式就是表里引用的 spec 的格式；问题去掉首尾空白后为空、复盘报告的总结去掉首尾空白后为空，都 MUST 是格式错误
- 网关只调用、收下输出，SHALL NOT 检查结论：结论的校验、备用模型的限制和结论里的信号由调用方按 case-types、model-fallback、signal-format spec 检查

本 spec 的场景除非另有说明，都使用默认配置和下面的基准数据：

- 当前时刻 2026-10-14 15:00Z（周三，盘中）
- 模型的配置是 model-fallback spec 的基准模型的配置，单价是 call-budget spec 的基准单价
- 案件：K-1 是 BBB（关注）的关注股事件，事件 E-1 是 BBB 的入场条件满足事件，要求的子 agent 是 Quant；K-3 是 CCC（持仓）的持仓事件；K-5 是每周复盘。S1 是 K-1 的第 1 版快照
- K-1 的案件上下文：问题"入场机会是否出现？"，事件是 E-1，事件要求的子 agent 是 Quant，关注论点是 stock-pool spec 的基准关注论点，没有投资论点
- 任务请求、Task 和 Result 用 task-dispatch spec 的基准数据：价格请求、新闻请求、财报请求，新生成的 Task 依次是 T-1、T-2……，截止时间 2026-10-14 15:05Z
- "p-1 给出输出，用量 a/b"指向 p-1 的这次尝试拿到了输出，输入 a 个、输出 b 个 token；"p-1 失败（限流）"指这次尝试没有拿到输出，调用错误是限流，没有用量；"失败（服务端出错，用量 a/0）"指没有拿到输出，但厂商给出了用量
- 时刻没有写明时，都是当前时刻

#### Scenario: PM 的第一次调用

- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** PM 收到 K-1 的案件上下文、S1 和空的对话
- **AND** PM 交回派发任务或提交结论

#### Scenario: 持仓的案件上下文

- **WHEN** 调用 K-3 的 PM，K-3 的事件是 CCC 的新闻事件 CCC:2026-10-14:news
- **THEN** 案件上下文的问题是"这个事件是否削弱或推翻了论点？"，事件是 CCC:2026-10-14:news，它的类别是新闻，事件要求的子 agent 是 News，投资论点是 CCC 现在的投资论点，没有关注论点
- **AND** 最近结束的案件和待执行的跟进是调用方给出的 CCC 的

#### Scenario: 子 agent 只看到 Task 和快照

- **GIVEN** K-1 的 PM 派发价格请求，派发 T-1
- **WHEN** 调用 Quant 运行 T-1
- **THEN** Quant 收到 T-1 和 S1，没有案件上下文和别的 Result

#### Scenario: Reviewer 只看到快照

- **WHEN** 调用 K-5 的 Reviewer
- **THEN** Reviewer 收到 K-5 的快照，交回复盘报告

#### Scenario: 复盘报告为空

- **WHEN** Reviewer 交回的复盘报告的总结是 "  "
- **THEN** 格式错误
