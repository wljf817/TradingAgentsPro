# Spec Delta

## MODIFIED Requirements

### Requirement: 事件要求的子 agent

子 agent SHALL 有三个：News、Quant、Fundamentals。事件案件（持仓风险事件、持仓事件、关注股事件、候选股事件）SHALL 要求本案件至少向事件类别对应的子 agent 派过任务，这是下限，PM 可以多派：

| 事件类别 | 至少派给 |
| --- | --- |
| 价格和成交量异动 | Quant |
| 新闻 | News |
| 公告 | Fundamentals |
| 财报发布 | Fundamentals |
| 关注股的入场条件满足 | Quant |
| 持仓达到目标价或证伪条件触发 | Quant |

- 派发出去的任务就算，不论 Result 是完成、无信号、超时还是失败：超时或失败时 PM 在缺少该信息的情况下继续
- 被 Agent 网关拒绝的任务没有交给子 agent，SHALL NOT 算派过任务（task-dispatch spec「本案件的任务和用量」）
- 其他案件类型没有这项要求
- 是否满足由「结论的校验」检查

#### Scenario: 新闻事件至少派给 News

- **WHEN** CCC 的新闻事件开始运行
- **THEN** 案件要求至少派过任务的子 agent 是 News

#### Scenario: 公告和财报发布至少派给 Fundamentals

- **WHEN** BBB 的公告事件开始运行；BBB 的财报发布事件开始运行
- **THEN** 两个案件要求至少派过任务的子 agent 都是 Fundamentals

#### Scenario: 价格类事件至少派给 Quant

- **WHEN** AAA 的价格和成交量异动事件开始运行；BBB 的入场条件满足事件开始运行；CCC 的达到目标价或证伪条件触发事件开始运行
- **THEN** 三个案件要求至少派过任务的子 agent 都是 Quant

#### Scenario: 不是事件的案件没有这项要求

- **WHEN** CCC 的持仓复查日到期、BBB 的跟进、AAA 的有新候选、股票池候选不足开出的案件开始运行
- **THEN** 这些案件都没有要求至少派过任务的子 agent

#### Scenario: 被拒绝的任务不算派过

- **GIVEN** BBB 的公告事件开出的关注股事件（要求 Fundamentals），本案件派给 Fundamentals 的任务被 Agent 网关拒绝，派发出去的只有 News 和 Quant 的任务
- **WHEN** 提交关注的结论：继续关注
- **THEN** 被拒绝，原因是"缺少事件要求的子 agent"
