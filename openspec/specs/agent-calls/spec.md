# agent-calls Specification

## Purpose
Agent 网关调用 PM、Critic、三个子 agent 和 Reviewer：规定每个 agent 收到什么、交回什么，PM 和 Critic 的对话怎样一轮一轮记下，一次调用怎样按备用模型的步骤向模型尝试、每次尝试记下费用，以及子 agent 的任务怎样直接得到 Result。M4 由替身实现调用，M5 换成真实模型，接口不变。

## Requirements

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
| 事件类别 | 事件案件的事件类别（case-types spec「触发」）；其他案件没有 |
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
- 案件：K-1 是 BBB（关注）的关注股事件，事件类别是入场条件满足，要求的子 agent 是 Quant；K-3 是 CCC（持仓）的持仓事件；K-5 是每周复盘。S1 是 K-1 的第 1 版快照
- K-1 的案件上下文：问题"入场机会是否出现？"，事件类别入场条件满足，事件要求的子 agent 是 Quant，关注论点是 stock-pool spec 的基准关注论点，没有投资论点
- 任务请求、Task 和 Result 用 task-dispatch spec 的基准数据：价格请求、新闻请求、财报请求，新生成的 Task 依次是 T-1、T-2……，截止时间 2026-10-14 15:05Z
- "p-1 给出输出，用量 a/b"指向 p-1 的这次尝试拿到了输出，输入 a 个、输出 b 个 token；"p-1 失败（限流）"指这次尝试没有拿到输出，调用错误是限流，没有用量；"失败（服务端出错，用量 a/0）"指没有拿到输出，但厂商给出了用量
- 时刻没有写明时，都是当前时刻

#### Scenario: PM 的第一次调用

- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** PM 收到 K-1 的案件上下文、S1 和空的对话
- **AND** PM 交回派发任务或提交结论

#### Scenario: 持仓的案件上下文

- **WHEN** 调用 K-3 的 PM，K-3 的事件类别是新闻
- **THEN** 案件上下文的问题是"这个事件是否削弱或推翻了论点？"，事件类别新闻，事件要求的子 agent 是 News，投资论点是 CCC 现在的投资论点，没有关注论点
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

### Requirement: 对话的每一轮

PM 和 Critic 在一个案件里的对话 SHALL 按先后排列，每一轮是 agent 的一次回复和交回给它的结果，按下表对应：

| agent | 回复 | 交回的结果 |
| --- | --- | --- |
| PM | 派发任务 | 每个请求的 Task 和 Result，按请求的顺序：派发的是子 agent 运行以后的 Result，被拒绝的是被拒绝的 Result，复用的是被复用的任务和它的 Result |
| PM | 提交结论 | 结论校验不通过、退回重交的原因（case-stages spec「唤醒 PM 和结论」）；或者 Critic 的质询：这次审核，以及这次审核里补充研究的 Task 和 Result |
| PM | 回应 | Critic 下一次审核的质询，以及那次审核里补充研究的 Task 和 Result |
| Critic | 派发任务（补充研究） | 每个请求的 Task 和 Result，按请求的顺序 |
| Critic | 质询 | PM 对这轮质询的回应 |

- 对话只放已经交回结果的轮次：结论通过以后没有交给 PM 回应的（没有交易方案、Critic 通过或否决、退出类方案）、Critic 通过或否决的那一轮都没有下一次调用，不进对话
- 回复和交回的结果不按上表对应、派发任务交回的结果和请求的个数不同、Critic 的通过或否决有交回的结果，都 MUST 报错

#### Scenario: 派发任务交回每个请求的结果

- **GIVEN** K-1 的 PM 派发价格请求、AAA 的新闻请求、价格请求：派发 T-1，T-2 被拒绝，第三个请求复用 T-1；T-1 完成
- **WHEN** 再调用 K-1 的 PM
- **THEN** 对话有一轮：回复是这次派发任务，交回的结果依次是 T-1 和它完成的 Result、T-2 和被拒绝的 Result、T-1 和它完成的 Result

#### Scenario: 结论退回重交

- **GIVEN** 对话有派发任务的一轮；PM 提交关注的结论（开仓方案），校验不通过，原因是"研究不足"
- **WHEN** 再调用 K-1 的 PM
- **THEN** 对话依次有两轮：派发任务和交回的结果；提交的结论和原因"研究不足"

#### Scenario: Critic 的质询交给 PM 回应

- **GIVEN** K-1 的 PM 提交开仓方案，通过校验；Critic 派发新闻请求（T-3），T-3 完成；Critic 质询："止损太近"
- **WHEN** 调用 K-1 的 PM（回应）
- **THEN** PM 的对话最后一轮是：提交的结论，交回 Critic 的这次审核和 T-3 的 Task 和 Result
- **AND** Critic 的对话依次有：补充研究和 T-3 的 Task 和 Result

#### Scenario: 第二轮质询

- **GIVEN** 上一个场景之后 PM 维持
- **WHEN** 调用 K-1 的 Critic 做第二次审核
- **THEN** Critic 的对话依次是：补充研究和 T-3 的 Task 和 Result；第一次质询和 PM 维持的回应
- **WHEN** 接着 Critic 再次质询，不派补充研究；调用 K-1 的 PM（回应）
- **THEN** PM 的对话最后一轮是：维持的回应，交回 Critic 的第二次审核，没有补充研究

#### Scenario: 回复和结果不对应

- **WHEN** 构造这些轮次：PM 派发任务，交回结论退回的原因；PM 派发两个请求，交回一个 Task 和 Result；PM 回应，交回 Task 和 Result；Critic 通过，交回 PM 的回应；Critic 质询，交回 Task 和 Result
- **THEN** 都报错

### Requirement: 一次调用

Agent 网关调用一个 agent 时 SHALL 按 model-fallback spec 的步骤向模型尝试：

- 从本案件这个 agent 的起点开始（model-fallback spec「案件内不切回」），每一步向那一步的模型发一次请求，最多等那一步给出的超时（model-fallback spec「尝试的超时」）
- 拿到输出：这次调用成功，给出输出、实际使用的模型（model-fallback spec「实际使用的模型」）和这次调用的费用
- 没有拿到输出：按调用错误报告这次尝试失败，按下一步继续（重试、换下一个模型）；全部不可用时 SHALL 给出调用失败和 model-fallback 给出的说明，由调用方交给案件状态（case-stages spec「Critic 不可用」「系统故障」）
- 每次尝试拿到了用量（成功的，和失败但厂商给出了用量的）SHALL 按 call-budget spec「费用的记录」记一条，案件类型是快照里的；拿不到用量的不记。这次调用的费用是这些记录的费用之和
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

### Requirement: 子 agent 的任务

调用方 SHALL 把派发出去的任务和快照交给 Agent 网关运行。网关按「一次调用」调用任务的目标子 agent，调用带任务的截止时间（model-fallback spec「尝试的超时」），然后按下表生成这个任务的 Result（task-dispatch spec）：

| 调用的结果 | Result |
| --- | --- |
| 拿到输出 | 按 task-dispatch spec「子 agent 的输出变成 Result」：完成、无信号，或者格式不合法的失败；模型是实际使用的模型 |
| 全部不可用 | 失败，原因是调用失败，说明是 model-fallback 给出的说明（task-dispatch spec「调用失败」） |
| 到了截止时间停止 | 超时（task-dispatch spec「超时」） |
| 任务已经有 Result | 给出这个 Result，SHALL NOT 调用模型，也不记费用 |

- Result 的费用是这次调用的费用：各次尝试记下的费用之和
- 几个任务可以同时运行

本 requirement 的场景里，Quant 的主模型是 google/s-1，备用模型是 openai/s-2；"T-1"指 K-1 的 PM 派发价格请求派发出的 T-1。

#### Scenario: 拿到合法的信号

- **WHEN** 2026-10-14 15:01:30Z 运行 T-1，s-1 给出 signal-format spec 的基准信号，用量 8,000/1,000
- **THEN** 向 s-1 发了一次请求，超时 60 秒
- **AND** T-1 完成，输出是基准信号，模型 s-1、不是备用模型，费用 0.0049

#### Scenario: 输出格式不合法

- **WHEN** 运行 T-1，s-1 给出不是 JSON 的文字"看多，理由见上"，用量 8,000/1,000
- **THEN** T-1 失败，原因是格式不合法，模型 s-1，费用 0.0049
- **AND** 没有再向 s-1 或 s-2 发请求

#### Scenario: 换备用模型以后完成

- **WHEN** 运行 T-1，s-1 失败（连接失败），再失败（连接失败），再 s-2 给出基准信号，用量 8,000/1,000
- **THEN** T-1 完成，模型 s-2、是备用模型，费用 0.0049

#### Scenario: 全部不可用

- **WHEN** 2026-10-14 15:02:00Z 运行 T-1，s-1 和 s-2 都失败两次（连接失败）
- **THEN** T-1 失败，原因是调用失败，说明"模型都不可用：google/s-1 连接失败；openai/s-2 连接失败"，没有模型，费用 0

#### Scenario: 到了截止时间停止

- **WHEN** 2026-10-14 15:04:00Z 开始运行 T-1，s-1 在 15:04:10Z 失败（连接失败），15:05:00Z 又失败
- **THEN** 不再尝试；T-1 超时，没有模型

#### Scenario: 超时不超过离截止时间还剩的时间

- **WHEN** 2026-10-14 15:04:30Z 开始运行 T-1
- **THEN** 向 s-1 发的第一次请求超时 30 秒

#### Scenario: 已经有 Result 的任务不再调用

- **GIVEN** T-1 已经完成
- **WHEN** 再运行 T-1（比如崩溃以后重做）
- **THEN** 给出 T-1 已有的 Result；没有向任何模型发请求，没有记下费用
