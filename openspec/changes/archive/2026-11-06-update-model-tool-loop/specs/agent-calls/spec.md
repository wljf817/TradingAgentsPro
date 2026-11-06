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
| 跟进 | 跟进案件开出它的跟进：申请（case-types spec「跟进申请的格式」）和提出申请的案件的结论；其他案件没有 |

- PM 回应质询时 SHALL NOT 派发任务：Proposal 规定回应需要的新信息由 Critic 的补充研究提供
- 子 agent 只看到 Task 和快照，看不到案件上下文、别的任务和 Result
- 任务请求、结论、回应、审核的格式就是表里引用的 spec 的格式；问题去掉首尾空白后为空、复盘报告的总结去掉首尾空白后为空，都 MUST 是格式错误
- 网关只调用、收下输出，SHALL NOT 检查结论：结论的校验、备用模型的限制和结论里的信号由调用方按 case-types、model-fallback、signal-format spec 检查

本 spec 的场景除非另有说明，都使用默认配置和下面的基准数据：

- 当前时刻 2026-10-14 15:00Z（周三，盘中）
- 任务时限和单次调用超时用 model-fallback spec 的基准数据：任务时限 5 分钟，子 agent 60 秒，PM、Critic、Reviewer 180 秒
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
- **AND** 最近结束的案件和待执行的跟进是调用方给出的 CCC 的；没有跟进

#### Scenario: 跟进案件的案件上下文

- **WHEN** 调用 CCC 的持仓跟进 K-8 的 PM，K-8 是跟进 F-1 开出的：F-1 的申请是 case-types spec 的基准跟进申请，提出申请的是 K-3，K-3 的结论是持有，附基准跟进申请
- **THEN** 案件上下文的问题是"竞争对手 11 月 18 日发财报，之后对这只股票有什么影响？"，没有事件和事件要求的子 agent，投资论点是 CCC 现在的投资论点
- **AND** 跟进是基准跟进申请和 K-3 的结论

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

### Requirement: 一次调用

Agent 网关调用一个 agent 时 SHALL 按 model-fallback spec 的步骤向模型尝试：

- 从本案件这个 agent 的起点开始（model-fallback spec「案件内不切回」），每一步向那一步的模型尝试一次，最多等那一步给出的超时（model-fallback spec「尝试的超时」）；一次尝试可以发多个请求（model-adapter spec「一次尝试」），场景里的"发了一次请求"指没有工具循环的尝试
- 拿到输出：这次调用成功，给出输出、实际使用的模型（model-fallback spec「实际使用的模型」）和这次调用的费用
- 没有拿到输出：按调用错误报告这次尝试失败，按下一步继续（重试、换下一个模型）；全部不可用时 SHALL 给出调用失败和 model-fallback 给出的说明，由调用方交给案件状态（case-stages spec「Critic 不可用」「系统故障」）
- 每次尝试拿到了用量（成功的，和失败但厂商给出了用量的）SHALL 按各请求之和，按 call-budget spec「费用的记录」记一条，案件类型是快照里的；拿不到用量的不记。这次调用的费用是这些记录的费用之和
- PM、Critic、Reviewer 的调用没有截止时间；子 agent 的见「子 agent 的任务」

#### Scenario: 主模型给出输出

- **WHEN** 调用 K-1 的 PM，p-1 给出输出，用量 20,000/2,000
- **THEN** 向 p-1 发了一次请求，超时 180 秒
- **AND** 调用成功，实际使用的模型是 p-1、不是备用模型；记下一条费用 0.06（K-1，关注股事件，PM，p-1），这次调用的费用 0.06

#### Scenario: 失败以后重试

- **WHEN** 调用 K-1 的 PM，p-1 失败（限流），再 p-1 给出输出，用量 20,000/2,000
- **THEN** 向 p-1 发了两次请求；只记下一条费用 0.06
- **AND** p-1 正常，没有记下不可用

#### Scenario: 换备用模型，失败的尝试有用量也记

- **WHEN** 调用 K-1 的 PM，p-1 失败（服务端出错，用量 10,000/0），再失败（服务端出错，用量 10,000/0），再 p-2 给出输出，用量 20,000/2,000
- **THEN** 依次向 p-1、p-1、p-2 发了请求；实际使用的模型是 p-2，是备用模型
- **AND** 记下三条费用：0.02、0.02、0.06；这次调用的费用 0.10

#### Scenario: 全部不可用

- **WHEN** 调用 K-5 的 Reviewer，p-1 失败（超时），再失败（超时）
- **THEN** 调用失败，说明是"模型都不可用：anthropic/p-1 超时"；没有记下费用

#### Scenario: Critic 用自己的模型

- **WHEN** 调用 K-1 的 Critic，c-1 给出输出，用量 30,000/3,000
- **THEN** 向 c-1 发了一次请求，超时 180 秒；实际使用的模型是 c-1；记下一条费用 0.0675，agent 是 Critic

#### Scenario: 案件内从起点开始

- **GIVEN** K-1 的 PM 调用过一次：p-1 不可用，p-2 给出输出
- **WHEN** 再调用 K-1 的 PM
- **THEN** 第一次请求发给 p-2

#### Scenario: 几个案件同时调用

- **WHEN** K-1 和 K-3 的 PM 同时调用，两次调用里 p-1 都失败两次（限流），再 p-2 都给出输出
- **THEN** 两次调用都成功，实际使用的模型都是 p-2；p-1 连续不可用 2 次，没有冷却
