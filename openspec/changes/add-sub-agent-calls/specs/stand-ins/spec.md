## MODIFIED Requirements

### Requirement: 子 agent 替身

News、Quant、Fundamentals 的替身 SHALL 对任何任务都交回替身的信号，引用 Task 的输入；输入为空时引用快照的市场状态条目。

#### Scenario: 引用 Task 的输入

- **WHEN** 经 Agent 网关运行 BBB 的 Quant 的任务
- **THEN** 任务完成，信号是替身的信号，引用 `s1/quote/BBB`，模型 `standin/quant`

#### Scenario: 输入为空

- **WHEN** 经 Agent 网关运行 CCC 的 News 的任务，输入为空
- **THEN** 任务完成，信号引用 `s1/market`
