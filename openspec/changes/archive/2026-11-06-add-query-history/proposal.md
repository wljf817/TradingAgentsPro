# Proposal

## Why

PM 的上下文里只有一只股票最近结束的 3 个案件，更早的案件、Critic 和风控的结果、交易和盈亏都看不到。PM 不自己算数，要判断这只股票以前做得怎样，就得有一个由代码查询、由代码算盈亏的只读工具。这是 PM 组最后一项，做完就可以写 prompt。

## What Changes

- 新增读取工具 `query_history`：PM 按股票查历史案件和交易；股票必填，不限于案件股票；起始日和案件类型可选
- 返回案件（类型、触发原因、结束原因、结论和论点、交易方案、Critic 的审核、风控结果）和交易，案件和交易各最多 20 条，并写明一共多少条
- 代码给出每笔卖出的已实现盈亏：按加权平均成本算，含费用，并给出合计
- 读取一律截到这次调用的快照的生成时刻：案件按结束时刻，交易按记录的写入时刻读那时的版本
- 研究和回应 Critic 时都能用；每次运行最多查 3 次，结果不进 PM 的对话，下次唤醒要看就再查
- 案件上下文加历史索引：这只股票的历史案件数和交易数
- 每次查询写一条程序日志
- 交易记录表加股票列，已有的行从记录内容回填

引用完整版 Proposal 的章节：「Agent」的「PM」里的「看到的内容」「工具」、「记录、复盘与经验」的「查询工具」「程序日志」「交易记录」、「共享上下文」的案件上下文一层。

**Non-goals：**

- 胜率等统计：在 M6
- `query_lessons` 和索引里的经验数：经验库在 M6
- Critic 使用这个工具：在拆出来的 Critic 变更里接
- 子 agent 的工具
- prompt 和宪章里怎样用这个工具：在「写 prompt」里写
- 把查询结果存进对话：见 design D2
- 寻股里这只股票的加入和删除记录：寻股案件没有股票，按股票查不到，要另写一套时间截止；候选评估、升级以后的论点和案件已经能查到

## Capabilities

### New Capabilities

- `history-query`：`query_history` 的参数、结果、时间截止、已实现盈亏和上限

### Modified Capabilities

- `agent-calls`：案件上下文加历史索引
- `case-workflow`：组出案件上下文时给出历史索引
- `pm-calls`：PM 有读取工具，工具次数上限来自配置
- `case-records`：读取一只股票已结束的全部案件
- `trade-records`：按股票读某个时刻的交易记录
- `state-store`：版本表可以按时刻读取
- `program-log`：Agent 网关写查询历史的条目
- `settings-file`：`gateway` 配置段的说明加上查询的配置

## Impact

- 代码：`contracts`（新增 `history.py`，`OrderStatus`、`TradeKind` 移到 `contracts`，`CaseContext` 加索引）、`modules/statestore`、`modules/caserecords`、`modules/execution`、`modules/gateway`、`modules/programlog`、`integrations/llm`、新增 `workflows/case_history.py`、`workflows/case_flow.py`、`bootstrap/app.py`
- 测试：`tests/contracts`、`tests/modules`、`tests/integrations/llm`、`tests/workflows`、`tests/bootstrap`
- 数据：迁移 `v0015` 给 `execution_trades` 加 `symbol` 列并回填
- 配置：`config/settings.toml` 多两个可选项，服务器不用改
- 部署：随下一次更新上服务器，启动时自动迁移；生产环境还是替身，替身不查询，只有上下文多一项索引
- 文档：完整版 Proposal「查询工具」把查询次数改成按每次运行算；两份路线表更新类型

## 需要人决定

无
