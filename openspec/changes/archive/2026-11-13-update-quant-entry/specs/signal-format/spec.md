# Spec Delta

## MODIFIED Requirements

### Requirement: 子 agent 引用的条目

子 agent 的输出通过「收到输出时的校验」以后，Agent 网关 SHALL 再检查它引用的条目：信号和无信号引用的每个条目都 MUST 是已知的条目，不是时格式不合法，原因是"引用了未知的条目"，并写明这些条目。

- 已知的条目 SHALL 是这次运行读的这版快照的全部条目，加上这次运行的工具返回过的内容的 ID（sub-agent-calls spec「工具返回过的内容」）；Quant 的任务另加上这个任务的量化指标条目的 ID（task-dispatch spec「Quant 任务的量化指标」）
- 不引用条目的无信号 SHALL 合法
- PM 结论里的信号 SHALL NOT 做这项检查

本 requirement 的场景里，这次运行读的快照是 S1，有条目 `s1/market`；工具返回过 `news/1`。

#### Scenario: 引用工具返回过的内容和快照的条目

- **WHEN** 收到引用 `news/1` 和 `s1/market` 的信号；或者收到引用 `news/1` 的无信号
- **THEN** 都合法

#### Scenario: 引用未知的条目

- **WHEN** 收到引用 `news/1` 和 `news/2` 的信号
- **THEN** 不合法，原因是"引用了未知的条目"，写明 `news/2`
- **WHEN** 收到引用 `s2/market` 的无信号
- **THEN** 不合法，原因是"引用了未知的条目"，写明 `s2/market`

#### Scenario: 不引用条目的无信号

- **WHEN** 收到基准无信号
- **THEN** 合法

#### Scenario: 先查评估窗口和数据截至时刻

- **WHEN** 收到评估窗口 4、引用 `news/2` 的信号
- **THEN** 不合法，原因是"评估窗口不在配置的选项里"

#### Scenario: PM 的信号不查引用

- **GIVEN** PM 的结论里的信号引用 `news/2`，其他都合法
- **WHEN** 收到 PM 的结论
- **THEN** 信号合法

#### Scenario: Quant 引用自己任务的量化指标条目

- **GIVEN** 这次运行的是 Quant 的任务 T-1，它的量化指标条目是 quant/T-1；同一个案件的另一个 Quant 任务 T-5 的条目是 quant/T-5
- **WHEN** 收到引用 quant/T-1 和 `s1/market` 的信号
- **THEN** 合法
- **WHEN** 收到引用 quant/T-5 的信号
- **THEN** 不合法，原因是"引用了未知的条目"，写明 quant/T-5
