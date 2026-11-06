## MODIFIED Requirements

### Requirement: 交回工具

调用 PM 时 SHALL 给出下表的交回工具（model-adapter spec「交回工具」），模型调用其中一个交出回复：

| 调用 | 交回工具 | 回复 |
| --- | --- | --- |
| 研究 | `dispatch_tasks` | 派发任务 |
| 研究 | `submit_conclusion` | 提交结论 |
| 回应 | `respond_to_challenge` | 回应 |

- 工具的参数格式 SHALL 是 agent-calls spec「每个 agent 的输入和输出」的格式
- PM 的读取工具是 `query_history`（history-query spec「查询工具」），工具次数上限是查询次数上限（history-query spec「查询的上限」）

#### Scenario: 研究时派发任务

- **GIVEN** 测试服务器回答调用 `dispatch_tasks`，参数合法
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 回复是派发任务，用量是这次回答的用量
- **AND** 请求里的交回工具只有 `dispatch_tasks` 和 `submit_conclusion`

#### Scenario: 研究时提交结论

- **GIVEN** 测试服务器回答调用 `submit_conclusion`，参数是结论
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** 回复是提交结论

#### Scenario: 回应质询

- **GIVEN** 对话最后一轮交回质询；测试服务器回答调用 `respond_to_challenge`，参数是维持
- **WHEN** 调用 K-1 的 PM（回应）
- **THEN** 回复是维持的回应
- **AND** 请求里的交回工具只有 `respond_to_challenge`
