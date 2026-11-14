# Spec Delta

## MODIFIED Requirements

### Requirement: 对话变成消息

PM 的对话 SHALL 按 agent-calls spec「对话的每一轮」一轮一轮变成下表的消息：

| 回复 | 模型的回答 | 交回给模型 |
| --- | --- | --- |
| 派发任务 | 调用 `dispatch_tasks`，参数是这次回复 | `dispatch_tasks` 的工具结果：每个请求的 Task、Result 和量化指标条目（agent-calls spec「对话的每一轮」）的 JSON |
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

#### Scenario: 工具结果带量化指标条目

- **GIVEN** 对话有派发的一轮，T-1 带量化指标条目 quant/T-1（task-dispatch spec「Quant 任务的量化指标」）
- **WHEN** 调用 K-1 的 PM（研究）
- **THEN** `dispatch_tasks` 的工具结果有 T-1、它完成的 Result 和 quant/T-1：条目 ID 和全部数字
