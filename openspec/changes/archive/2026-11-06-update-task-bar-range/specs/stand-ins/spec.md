## MODIFIED Requirements

### Requirement: PM 替身派发任务

PM 替身在研究阶段 SHALL 按下表决定派不派任务：

| 案件和结论 | 派给 |
| --- | --- |
| 结论是开仓方案或升级（见「PM 替身的结论」） | Quant 和 Fundamentals；事件案件另加事件要求的子 agent |
| 其他事件案件 | 事件要求的子 agent |
| 其他案件 | 不派 |

- 要派任务、对话里还没有派发任务时，交回派发任务：每个子 agent 一个请求，News 是事件影响评估，Quant 是价格反应，Fundamentals 是财报对比预期；按 News、Quant、Fundamentals 的顺序；股票是案件的股票；问题是固定的文字；输入是案件股票的行情条目 ID，快照里没有这个条目时为空；Quant 的任务周期是日线、范围是 60 个交易日
- 不派任务，或者对话里已经有派发任务时，交回提交结论；结论引用对话里全部状态是完成的 Result，按交回的先后、不重复
- 结论被退回重交时，再交回同样的结论

#### Scenario: 开仓方案派给 Quant 和 Fundamentals

- **WHEN** 调用 BBB 的入场条件满足开出的关注股事件的 PM 替身，对话为空
- **THEN** 交回派发任务：Quant 的任务、Fundamentals 的任务（入场条件满足要求的 Quant 已经在里面）
- **WHEN** 接着两个任务完成（T-1、T-2），再调用
- **THEN** 交回开仓方案，引用 T-1 和 T-2

#### Scenario: 升级派给 Quant 和 Fundamentals

- **WHEN** 调用 AAA 的候选评估的 PM 替身，对话为空
- **THEN** 交回派发任务：Quant 的任务、Fundamentals 的任务

#### Scenario: 其他事件案件派给事件要求的子 agent

- **WHEN** 调用 CCC 的新闻事件开出的持仓事件、CCC 的价格和成交量异动开出的持仓风险事件、AAA 的公告事件开出的候选股事件、BBB 的新闻事件开出的关注股事件的 PM 替身，对话都为空
- **THEN** 依次交回派发任务：CCC 的 News 的任务；CCC 的 Quant 的任务；AAA 的 Fundamentals 的任务；BBB 的 News 的任务

#### Scenario: 其他案件不派任务

- **WHEN** 调用 CCC 的持仓复查、BBB 的关注复查、寻股、CCC 的持仓跟进的 PM 替身，对话都为空
- **THEN** 都直接交回提交结论，不引用 Result

#### Scenario: 案件股票没有行情条目

- **GIVEN** CCC 的新闻事件开出的持仓事件，快照里没有 CCC 的行情条目
- **WHEN** 调用 PM 替身，对话为空
- **THEN** 交回 CCC 的 News 的任务，输入为空

#### Scenario: 只派一次，被拒绝或超时的 Result 不引用

- **GIVEN** BBB 的入场条件满足开出的关注股事件，对话有一轮派发任务：T-1 完成，T-2 超时
- **WHEN** 调用 PM 替身
- **THEN** 交回开仓方案，只引用 T-1，不再派任务

#### Scenario: 退回以后再交同样的结论

- **GIVEN** 上一个场景的开仓方案被退回，原因是"研究不足"
- **WHEN** 再调用 PM 替身
- **THEN** 交回和上一次相同的开仓方案
