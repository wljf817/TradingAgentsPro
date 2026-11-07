# sub-agent-calls Specification

## Purpose
规定向真实模型调用 News 和 Fundamentals 时发什么、收什么：系统指令和消息、子 agent 的上下文、交回工具、工具返回过的内容、工具次数、输出不合法变成什么，以及角色 prompt 文件。

## Requirements

### Requirement: 调用子 agent 的消息

向真实模型调用 News 或 Fundamentals 时，SHALL 发下面的内容，按表里的顺序：

| 部分 | 内容 |
| --- | --- |
| 系统指令 | 系统宪章的文字（shared-context spec「系统宪章」）、一个空行、这个子 agent 的角色 prompt |
| 用户消息 | 这次调用的子 agent 的上下文的 JSON，只有这一条 |

- 系统指令 SHALL 只放宪章和角色 prompt，任务和快照的内容 MUST NOT 进系统指令
- 向真实模型调用 Quant SHALL 报错：Quant 还没有接上真实模型

本 spec 的场景除非另有说明，都使用 agent-calls spec 的基准数据，另外：

- 厂商 acme 的接口是 `anthropic`，厂商 orbit 的接口是 `openai`，地址都是测试服务器；News 的模型是 acme/m-1；没有写明厂商的场景两家各调用一次
- 系统宪章的文字是"宪章"，News 的角色 prompt 是"新闻角色"，Fundamentals 的角色 prompt 是"基本面角色"
- T-2 是 K-1 的 PM 派发的新闻请求，股票 BBB，输入 `s1/news/1`；S1 有条目 `s1/market`、`s1/quote/BBB`、`s1/news/1` 和事件 `s1/event/BBB:2026-10-14:news`

#### Scenario: News 的调用

- **WHEN** 调用 News 运行 T-2
- **THEN** 系统指令是"宪章"、空行、"新闻角色"
- **AND** 消息只有一条用户消息，是 T-2 的上下文的 JSON

#### Scenario: Fundamentals 的调用

- **WHEN** 调用 Fundamentals 运行 K-1 的财报请求
- **THEN** 系统指令是"宪章"、空行、"基本面角色"

#### Scenario: Quant 还没有接上

- **WHEN** Quant 的模型是 acme/m-1，调用它运行 K-1 的价格请求
- **THEN** 报错，没有发请求

### Requirement: 子 agent 的上下文

子 agent 的上下文 SHALL 由 Task 和这次运行读的这版快照组出，只包含下表的内容，条目 ID 和数字都按快照的原样：

| 项 | 内容 |
| --- | --- |
| 任务 | 任务类型、股票和问题 |
| 快照时刻 | 这版快照的生成时刻 |
| 市场状态 | 快照的市场状态条目 |
| 行情 | 这只股票的行情条目，带最新价和前收盘价；快照里没有时为空 |
| 相关事件 | 快照的全部事件条目 |
| 输入 | Task 的输入在这版快照里查到的条目，内容原样，新闻和公告都是全文 |
| 没查到的输入 | Task 的输入在这版快照里查不到的 ID |

- 账户、持仓、现金、股票池、案件上下文、别的新闻和公告、财报和 surprise、别的任务和 Result MUST NOT 进上下文，写在输入里的条目除外
- 寻股任务的股票没有行情时，行情为空，没有相关事件

#### Scenario: 持仓和股票池不进上下文

- **GIVEN** S1 的账户有 CCC 的持仓，股票池有 AAA、BBB、CCC，有 BBB 的另一条新闻 `s1/news/2`
- **WHEN** 组出 T-2 的上下文
- **THEN** 上下文有任务（事件影响评估、BBB、问题）、S1 的生成时刻、`s1/market`、`s1/quote/BBB`、`s1/event/BBB:2026-10-14:news`
- **AND** 输入有 `s1/news/1` 的全文，没查到的输入为空
- **AND** 没有账户、持仓、股票池和 `s1/news/2`

#### Scenario: 寻股的候选没有行情

- **GIVEN** 寻股 K-2 的第 1 版快照里没有 DDD 的行情
- **WHEN** 组出 K-2 的 DDD 的财报请求的上下文
- **THEN** 行情为空，相关事件为空，其他项照常

#### Scenario: 输入查不到

- **GIVEN** 任务的输入是 `s1/news/1` 和 `s1/news/9`，`s1/news/9` 不在这版快照里
- **WHEN** 组出上下文
- **THEN** 输入有 `s1/news/1`，没查到的输入是 `s1/news/9`

#### Scenario: 新的一版快照

- **GIVEN** K-1 有第 2 版快照，Critic 派的任务读第 2 版，输入是 `s1/news/1`
- **WHEN** 组出上下文
- **THEN** 市场状态是 `s2/market`；没查到的输入是 `s1/news/1`

### Requirement: 交回工具

News 和 Fundamentals SHALL 只用下面两个工具交出结果：

| 工具 | 结果 |
| --- | --- |
| `submit_signal` | 信号（signal-format spec「信号的格式」） |
| `submit_no_signal` | 无信号（signal-format spec「无信号」） |

#### Scenario: 交回信号

- **GIVEN** 测试服务器回答调用 `submit_signal`，参数是基准信号，用量输入 8,000、输出 1,000
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出是基准信号，用量输入 8,000、输出 1,000，只有一个请求

#### Scenario: 交回无信号

- **GIVEN** 测试服务器回答调用 `submit_no_signal`，参数是基准无信号
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出是基准无信号

### Requirement: 工具返回过的内容

调用子 agent 时 SHALL 记下这次运行的读取工具返回过的内容的 ID，和输出一起交给 Agent 网关（signal-format spec「子 agent 引用的条目」）。

- 这些 ID SHALL NOT 带快照版本的前缀，MUST NOT 和快照的条目 ID 相同
- 只算这次运行：换模型或者重试时重新开始，失败的运行返回过的内容不算
- 读取工具按子 agent 注册：News 的见 news-tools spec，Fundamentals 现在没有读取工具

本 requirement 的场景里，News 注册的读取工具换成 lookup，返回 `news/1` 的内容。

#### Scenario: 调用工具以后交回

- **GIVEN** 测试服务器第一次回答调用 lookup，第二次回答调用 `submit_signal`
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出和 `news/1` 一起交给网关

#### Scenario: 没有调用工具

- **GIVEN** 测试服务器回答调用 `submit_no_signal`
- **WHEN** 调用 News 运行 T-2
- **THEN** 工具返回过的内容为空

#### Scenario: 换备用模型不带上一次的内容

- **GIVEN** News 的备用模型是 orbit/m-2；acme/m-1 调用 lookup 以后服务端出错，两次都这样；orbit/m-2 没有调用工具，给出引用 `news/1` 的信号
- **WHEN** 经 Agent 网关运行 T-2
- **THEN** T-2 失败，原因是格式不合法，说明写明未知的条目 `news/1`，模型 orbit/m-2、是备用模型

### Requirement: 工具次数

一次运行的读取工具调用次数上限 SHALL 由配置 `max_sub_agent_tool_calls` 决定，默认 5，正整数，按每次运行算：重试和换模型都重新计数。用完以后的处理见 model-adapter spec「工具次数上限」。

#### Scenario: 用完次数

- **GIVEN** `max_sub_agent_tool_calls` 是 2，测试服务器前三次回答调用 lookup，第四次回答调用 `submit_signal`
- **WHEN** 调用 News 运行 T-2
- **THEN** 代码执行 lookup 两次，第三次调用的结果是提示，输出是这个信号

#### Scenario: 重试重新计数

- **GIVEN** `max_sub_agent_tool_calls` 是 2；acme/m-1 第一次运行调用 lookup 两次以后服务端出错
- **WHEN** 经 Agent 网关运行 T-2，第二次运行调用 lookup 两次以后交回信号
- **THEN** 第二次运行的 lookup 都执行，T-2 完成

### Requirement: 子 agent 的输出不合法

模型的回答不是合法的 `submit_signal` 或 `submit_no_signal` 时，这次调用的输出 SHALL 是输出不合法，带原因，原因写法同 pm-calls spec「PM 的输出不合法」：

| 情况 | 原因 |
| --- | --- |
| 只回文字 | 没有调用交回工具 |
| 回答是空的 | 没有调用交回工具 |
| 交回工具的参数不合法 | 每个不合法的字段和错误 |
| 工具的参数不合法 | 每个不合法的字段和错误 |
| 调用这次没有给出的工具 | 工具不存在 |

- 输出不合法 SHALL 算这次尝试成功（model-fallback spec「什么算不可用」）：用量照记，不重试，不切换模型
- 调用错误 SHALL 照样按 model-adapter spec「调用错误」交回网关，不是输出不合法

#### Scenario: 只回文字

- **GIVEN** 测试服务器回答文字 `thinking`，用量输入 11、输出 7
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出不合法，原因写明没有调用交回工具，用量输入 11、输出 7
- **AND** 只有一个请求

#### Scenario: 空回答

- **GIVEN** 测试服务器回答的内容为空
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出不合法，原因写明没有调用交回工具

#### Scenario: 交回工具的参数不合法

- **GIVEN** 测试服务器回答调用 `submit_signal`，参数没写主要风险
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出不合法，原因写明主要风险

#### Scenario: 工具的参数不合法

- **GIVEN** News 注册了读取工具 lookup；测试服务器回答调用 lookup，参数不符合它的格式
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出不合法，原因写明这个参数

#### Scenario: 调用没有给出的工具

- **GIVEN** 测试服务器回答调用 `dispatch_tasks`
- **WHEN** 调用 News 运行 T-2
- **THEN** 输出不合法，原因写明工具不存在

#### Scenario: 调用错误不是输出不合法

- **GIVEN** 测试服务器回答 429
- **WHEN** 调用 News 运行 T-2
- **THEN** 调用错误是限流

### Requirement: 子 agent 的角色 prompt

程序启动时 SHALL 读入 News 和 Fundamentals 的角色 prompt 文件，内容是文件去掉首尾空白以后的文字。

- 文件读不到，或者去掉首尾空白以后为空，SHALL 启动失败
- 程序运行期间角色 prompt SHALL NOT 变化

#### Scenario: 读入角色 prompt

- **GIVEN** News 的角色 prompt 文件的内容是"  新闻角色\n"
- **WHEN** 程序启动
- **THEN** News 的系统指令里的角色 prompt 是"新闻角色"

#### Scenario: 角色 prompt 不合法

- **WHEN** Fundamentals 的角色 prompt 文件不存在；或者内容只有空白
- **THEN** 启动失败
