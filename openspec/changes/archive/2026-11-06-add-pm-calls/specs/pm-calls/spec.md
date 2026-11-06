## Purpose

规定向真实模型调用 PM 时发什么、收什么：系统指令和消息的顺序、对话怎样变成消息、交回工具、输出不合法变成什么，以及 PM 的角色 prompt 文件。

## ADDED Requirements

### Requirement: 调用 PM 的消息

向真实模型调用 PM（研究或回应）时，SHALL 发下面的内容，按表里的顺序：

| 部分 | 内容 |
| --- | --- |
| 系统指令 | 系统宪章的文字（shared-context spec「系统宪章」）、一个空行、PM 的角色 prompt |
| 第一条用户消息 | 这次调用的 PM 的上下文（shared-context spec「PM 的上下文」）的 JSON |
| 之后的消息 | PM 在本案件的对话，按先后（见「对话变成消息」） |

- 消息 SHALL 到对话的最后一轮为止，后面 MUST NOT 再加消息，模型接着回答
- 系统指令 SHALL 只放宪章和角色 prompt，案件的内容 MUST NOT 进系统指令
- 第一条用户消息 SHALL 按这次调用的快照组出：案件有了新的一版快照时，上下文用新的一版，对话不变

本 spec 的场景除非另有说明，都使用 shared-context spec 的基准数据，另外：

- 厂商 acme 的接口是 `anthropic`，厂商 orbit 的接口是 `openai`，地址都是测试服务器；PM 的模型是 acme/m-1；没有写明厂商的场景两家各调用一次
- 系统宪章的文字是"宪章"，PM 的角色 prompt 是"角色"
- 上下文 C 是 K-1 的 PM 的上下文的 JSON，对话为空
- "派发"是 PM 派发价格请求，T-1 完成；"结论"是 K-1 的开仓结论

#### Scenario: 第一次调用

- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 系统指令是"宪章"、空行、"角色"
- **AND** 消息只有一条用户消息 C

#### Scenario: 有对话的调用

- **GIVEN** 对话有派发的一轮
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 消息依次是：用户消息 C，调用 `dispatch_tasks`，它的工具结果；后面没有别的消息

#### Scenario: 新的一版快照

- **GIVEN** 对话有派发的一轮，K-1 有第 2 版快照
- **WHEN** 用第 2 版调用 K-1 的 PM（回应）
- **THEN** 第一条用户消息是第 2 版组出的上下文，条目 ID 都以 s2/ 开头；后面的对话不变

### Requirement: 对话变成消息

PM 的对话 SHALL 按 agent-calls spec「对话的每一轮」一轮一轮变成下表的消息：

| 回复 | 模型的回答 | 交回给模型 |
| --- | --- | --- |
| 派发任务 | 调用 `dispatch_tasks`，参数是这次回复 | `dispatch_tasks` 的工具结果：每个请求的 Task 和 Result 的 JSON |
| 提交结论 | 调用 `submit_conclusion`，参数是这次回复 | `submit_conclusion` 的工具结果：退回的原因，或者 Critic 的质询和补充研究的 JSON |
| 回应 | 调用 `respond_to_challenge`，参数是这次回应 | `respond_to_challenge` 的工具结果：Critic 下一次质询和补充研究的 JSON |
| 输出不合法 | 文字：模型当时的原文 | 用户消息：退回的原因的 JSON |

- 工具调用和它的工具结果 SHALL 用同一个 ID，一段对话里的 ID 各不相同
- 消息 MUST NOT 带思考内容（model-adapter spec「之前的对话」）
- 相同的对话 SHALL 变成完全相同的消息

#### Scenario: 派发任务的一轮

- **GIVEN** 对话有派发的一轮
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 模型的回答调用 `dispatch_tasks`，参数是价格请求
- **AND** 工具结果有 T-1 和它完成的 Result，ID 和这次工具调用相同

#### Scenario: 结论退回的一轮

- **GIVEN** 对话最后一轮是结论，退回的原因是"研究不足"
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 最后两条消息是：调用 `submit_conclusion`，参数是结论；工具结果有"研究不足"

#### Scenario: 质询的一轮

- **GIVEN** 对话最后一轮是结论，交回 Critic 的质询"止损太近"和 T-3 的 Task 和 Result
- **WHEN** 调用 K-1 的 PM（回应）
- **THEN** 最后一条消息是 `submit_conclusion` 的工具结果，有"止损太近"和 T-3 的 Result

#### Scenario: 回应的一轮

- **GIVEN** 对话最后一轮是维持的回应，交回 Critic 的第二次质询
- **WHEN** 调用 K-1 的 PM（回应）
- **THEN** 最后两条消息是：调用 `respond_to_challenge`，参数是维持的回应；工具结果有第二次质询

#### Scenario: 输出不合法的一轮

- **GIVEN** 对话最后一轮是输出不合法，原文 `not json`，原因"不是工具调用"
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 最后两条消息是：模型的文字 `not json`；用户消息有"不是工具调用"

#### Scenario: 几轮的 ID 不同

- **GIVEN** 对话有两轮派发
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 两次工具调用的 ID 不同，各自和自己的工具结果相同

#### Scenario: 相同的对话

- **WHEN** 用同样的上下文和对话调用两次
- **THEN** 两次的消息完全相同

### Requirement: 交回工具

调用 PM 时 SHALL 给出下表的交回工具（model-adapter spec「交回工具」），模型调用其中一个交出回复：

| 调用 | 交回工具 | 回复 |
| --- | --- | --- |
| 研究 | `dispatch_tasks` | 派发任务 |
| 研究 | `submit_conclusion` | 提交结论 |
| 回应 | `respond_to_challenge` | 回应 |

- 工具的参数格式 SHALL 是 agent-calls spec「每个 agent 的输入和输出」的格式
- PM 没有读取工具，工具次数上限是 0

#### Scenario: 研究时派发任务

- **GIVEN** 测试服务器回答调用 `dispatch_tasks`，参数合法
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 回复是派发任务，用量是这次回答的用量
- **AND** 请求里的工具只有 `dispatch_tasks` 和 `submit_conclusion`

#### Scenario: 研究时提交结论

- **GIVEN** 测试服务器回答调用 `submit_conclusion`，参数是结论
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 回复是提交结论

#### Scenario: 回应质询

- **GIVEN** 对话最后一轮交回质询；测试服务器回答调用 `respond_to_challenge`，参数是维持
- **WHEN** 调用 K-1 的 PM（回应）
- **THEN** 回复是维持的回应
- **AND** 请求里的工具只有 `respond_to_challenge`

### Requirement: PM 的输出不合法

模型的回答不能变成回复时，这次调用 SHALL 给出输出不合法：模型的原文和原因。原因写明哪里不合法，交给 PM 修改：

| 情况 | 原文 | 原因 |
| --- | --- | --- |
| 只回文字 | 这段文字 | 没有调用交回工具 |
| 工具的参数不合法 | 工具的参数 | 每个不合法的字段和错误 |
| 调用这次没有给出的工具 | 工具的参数 | 工具不存在 |
| 回答是空的 | "（空回答）" | 没有调用交回工具 |

- 输出不合法 SHALL 算这次尝试成功（model-fallback spec「什么算不可用」）：用量照记，不重试，不切换模型
- 调用错误 SHALL 照样按 model-adapter spec「调用错误」交回网关，不是输出不合法

#### Scenario: 只回文字

- **GIVEN** 测试服务器回答文字 `thinking`，用量输入 11、输出 7
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 回复是输出不合法，原文 `thinking`，原因写明没有调用交回工具，用量输入 11、输出 7
- **AND** 只有一个请求

#### Scenario: 工具的参数不合法

- **GIVEN** 测试服务器回答调用 `submit_conclusion`，参数里的开仓方案没写止损价
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 回复是输出不合法，原文是这些参数，原因写明止损价

#### Scenario: 回应时调用研究的工具

- **GIVEN** 对话最后一轮交回质询；测试服务器回答调用 `dispatch_tasks`
- **WHEN** 调用 K-1 的 PM（回应）
- **THEN** 回复是输出不合法

#### Scenario: 空回答

- **GIVEN** 测试服务器回答的内容为空
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 回复是输出不合法，原文"（空回答）"

#### Scenario: 调用错误不是输出不合法

- **GIVEN** 测试服务器回答 429
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 调用错误是限流

### Requirement: PM 的角色 prompt

程序启动时 SHALL 读入 PM 的角色 prompt 文件，内容是这个文件去掉首尾空白以后的文字。

- 文件随代码发布
- 文件读不到、去掉首尾空白后为空，都 MUST 启动报错
- 程序运行期间角色 prompt SHALL NOT 变化

#### Scenario: 读入角色 prompt

- **GIVEN** 文件内容是"角色"，前后有空行
- **WHEN** 程序启动
- **THEN** PM 的系统指令里的角色 prompt 是"角色"

#### Scenario: 角色 prompt 不合法

- **WHEN** 文件不存在；或者内容是"  "
- **THEN** 都启动报错
