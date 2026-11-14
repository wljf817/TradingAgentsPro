# Spec Delta

## MODIFIED Requirements

### Requirement: 子 agent 替身

News 和 Fundamentals 的替身 SHALL 对任何任务都交回替身的信号，引用 Task 的输入；输入为空时引用快照的市场状态条目。Quant 的替身 SHALL 对任何任务都交回替身的信号，只引用这个任务的量化指标条目（task-dispatch spec「Quant 任务的量化指标」）。

#### Scenario: 引用 Task 的输入

- **WHEN** 经 Agent 网关运行 BBB 的 Fundamentals 的任务，输入是 `s1/quote/BBB`
- **THEN** 任务完成，信号是替身的信号，引用 `s1/quote/BBB`，模型 `standin/fundamentals`

#### Scenario: 输入为空

- **WHEN** 经 Agent 网关运行 CCC 的 News 的任务，输入为空
- **THEN** 任务完成，信号引用 `s1/market`

#### Scenario: Quant 引用量化指标条目

- **WHEN** 经 Agent 网关运行 BBB 的 Quant 的任务 T-1，输入是 `s1/quote/BBB`
- **THEN** 任务完成，信号是替身的信号，只引用 T-1 的量化指标条目（`quant/` 加上 T-1 的 task_id），模型 `standin/quant`
