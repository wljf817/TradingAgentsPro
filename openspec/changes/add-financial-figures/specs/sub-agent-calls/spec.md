## MODIFIED Requirements

### Requirement: 工具返回过的内容

调用子 agent 时 SHALL 记下这次运行的读取工具返回过的内容的 ID，和输出一起交给 Agent 网关（signal-format spec「子 agent 引用的条目」）。

- 这些 ID SHALL NOT 带快照版本的前缀，MUST NOT 和快照的条目 ID 相同
- 只算这次运行：换模型或者重试时重新开始，失败的运行返回过的内容不算
- 读取工具按子 agent 注册：News 的见 news-tools spec，Fundamentals 的见 filing-tools spec 和 financials-tool spec

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
