# Design

## Context

原因见 proposal.md。`LlmClient.run` 已经能带读取工具和次数上限（`ReadTool`、`_ToolLimit`），PM 现在传的是 0。案件记录有股票、类型、状态和结束时刻的筛选列；交易记录表 `execution_trades` 只有键和 `traded`，`VersionedStore` 只能读最新版本或一段时间的版本。

PM 的对话在工作流的 `case.pm_turns` 里，每轮是 `PmTurn(reply, feedback)`，每次调用由代码重建，一次运行里的工具调用不会留下来。

模块边界：

- `contracts`：新增 `history.py`（`HistoryIndex`、`HistoryCase`、`HistoryTrade`、`HistoryReport`）；`OrderStatus`、`TradeKind` 移到这里；`CaseContext` 加 `history: HistoryIndex | None`
- `modules/statestore`：`VersionedStore.as_of`
- `modules/caserecords`：`ended(symbol)`
- `modules/execution`：`trades_at(symbol, at)`，`domain` 算已实现盈亏，给出 `HistoryTrade`
- `modules/gateway`：端口 `HistoryReader`，用例 `HistoryQueryService`（查询、写程序日志），配置两项
- `modules/programlog`：动作 `QUERY_HISTORY`
- `workflows`：`CaseHistory` 满足 `HistoryReader`，组合案件和交易；`CaseFlow.context` 用它给出索引
- `integrations/llm`：`pm.py` 按请求组出 `ReadTool`，`LlmBackend` 带上它
- `bootstrap`：装配，迁移 `v0015`

```
模型调用 query_history(symbol, since, case_type)
  --> ReadTool（闭包带 case_id、taken_at）
  --> HistoryQueryService.query --> HistoryReader.report --> HistoryReport
          |                            |-- CaseRecordService.ended + proposal
          |                            `-- ExecutionService.trades_at
          `--> ProgramLog
  --> HistoryReport 的 JSON 交回模型
```

## Goals / Non-Goals

**Goals:**

- PM 能按股票查到案件、论点、Critic 和风控的结果、交易和盈亏，数字都由代码给出
- 回放时用同一套读取，看不到快照之后的内容

**Non-Goals:**

- 见 proposal.md 的 Non-goals

## Decisions

### D1 查询经网关，组合放在 workflows

Proposal 规定 PM 经 Agent 网关使用查询工具，程序日志也写在网关的动作下，所以入口是网关的 `HistoryQueryService`。它要同时读案件记录和执行两个模块，而跨模块的组合只能在 workflows，所以网关定义端口 `HistoryReader`，`workflows/case_history.py` 的 `CaseHistory` 实现它，bootstrap 注入。

没有放进 `AgentCallService`：它持有 `ModelBackend`，而工具是 backend 调用的，放进去就成了循环依赖。

### D2 结果不进对话，次数按一次运行算

Proposal 写的是每个案件的查询次数有上限，这里改成按一次运行算，完整版 Proposal 跟着改。

对话由 `PmTurn` 重建，工具结果不在里面。按案件算次数的话，第一次唤醒查过的内容第二次唤醒看不到，次数又用完了，PM 就再也拿不到。

- 选的做法：不保存，要看就再查。查询只读、截到快照时刻，同一版快照查两次结果相同，多花的只是 token；每个案件的运行次数有限（派发轮次、重交次数、质询轮数），查询总数也有上限
- 没选的做法：把工具调用存进 `PmTurn`。要让 `ModelReply` 带回工具调用、改对话的格式和 Workflow 里保存的内容，换来的只是省 token，而费用不是约束

### D3 上限各算各的，总数照实写

条数上限对案件和交易分别生效，返回最新的那些，另写总数，让 PM 知道有没有没给出的。`since` 只有下限，案件超过上限时更早的只能靠案件类型缩小范围，看 Risks。

### D4 时间截止用这次调用的快照

闭包里的时刻是 `request.snapshot.taken_at`，不是当前时刻，也不是第 1 版快照：Critic 要求补充研究时有新的一版快照，上下文用新的一版（pm-calls spec「调用 PM 的消息」），查询跟它一致。

案件不用按版本读：结束以后记录和方案记录都不再变，筛结束时刻早于 T 就是那时的内容。方案记录在交给下单以前写完，案件才结束。

交易要按版本读：一条记录会在快照之后继续成交，最新版本已经包含了未来。

### D5 `VersionedStore.as_of` 和股票列

`as_of(at, **where)`：先取每个键 `recorded_at < at` 的最新版本，再按 `where` 筛。筛选在取版本之后，`traded` 看的是那时的值。和 `history` 的"早于 T"一致。

`execution_trades` 加 `symbol` 列，迁移 `v0015` 三步：加可空列；`UPDATE execution_trades SET symbol = data->>'symbol'`；改成不可空。每个版本都有内容，回填不会缺。`PostgresTradeStore.save` 写入这一列。

交易记录数量很小，不加索引。

### D6 已实现盈亏放在 execution 的 domain

纯函数，输入一只股票时刻 T 的全部 `Trade`，算法见 history-query spec「已实现盈亏」。

- 一条交易记录是一笔订单的累计，没有逐笔成交，所以按成交日排序。同一天先算买入：止损单的创建时刻可以早于当天的加仓，先算卖出会让卖出股数超过持股数
- 同一天先卖后买时，卖出按混合后的平均成本算，有偏差但不影响持股数；账户每天这种情况极少
- 选加权平均成本，不选 FIFO：和券商持仓的成本口径一致，PM 上下文里的持仓成本也是均价
- 结果保留两位小数，用 `ROUND_HALF_UP`；合计是保留以后的值相加，和 PM 看到的数对得上

M6 要算每笔交易的结果时可以复用这个函数。

### D7 起始日是美东日期

模型写日期比写带时区的时刻不容易错。案件用结束时刻转到 `America/New_York` 的日期比较，交易用成交日比较。这个转换放在 `workflows/case_history.py`：不依赖本机时区，也不进 domain。

没有用交易日历：起始日不一定是交易日，这里只是日期比较，不是交易日的判断。

### D8 案件读全部再筛

`CaseRecordStore.ended(symbol)` 用 `VersionedStore.latest(symbol=..., status=...)` 读出这只股票已结束的全部案件，截止、起始日、类型和排序都在 `CaseHistory` 里做。一只股票的案件是几百条的量级，一次读出比给 `VersionedStore` 加范围查询简单，总数也顺带有了。方案记录只读要返回的那些，所以先截断再读。

### D9 `OrderStatus`、`TradeKind` 移到 contracts

`HistoryTrade` 要用这两个枚举，contracts 不能依赖 modules。`execution` 的 domain 从 contracts 导入，包根照常导出，用到它们的地方不用改。

### D10 配置放在 `GatewayConfig`

`max_history_queries`、`max_history_rows`，都是 `PositiveInt`。次数上限由 bootstrap 传给 `LlmBackend`，条数上限传给 `HistoryQueryService`，再作为 `HistoryReader.report` 的参数。

### D11 索引在开案件时算一次

`CaseFlow.context` 用第 1 版快照的 `taken_at` 调 `CaseHistory.index(symbol, before)`，和可用额度读同一版快照，之后每次调用用同一份案件上下文。索引是开案件时的数，不随新快照变，和最近结束的案件一样。

`CaseServices` 加 `history: CaseHistory`。

### D12 工具的参数

`ReadTool` 的参数格式取自函数签名：`query_history(symbol: str, since: date | None = None, case_type: CaseType | None = None) -> str`，返回 `HistoryReport.model_dump_json()`。参数不合法走 model-adapter spec「读取工具」，是输出不合法，退回 PM 重交。

工具的说明只写一句，和交回工具一样，不算 prompt；怎样用写在「写 prompt」里。

### 省略的情况

- 股票代码不做大小写转换：模型上下文里的代码都是大写，写错就是查不到
- 卖出股数超过持股数：系统专用账户不会发生，出现时报错或算出负成本，不处理
- 持股数为 0 时的卖出：同上，除以 0 报错，按工具出错结束这次运行
- 查询和写日志时数据库出错：原样抛出，走 model-adapter spec「读取工具」的工具出错
- 崩溃恢复重新执行调用时再写一条日志：确实又查了一次

## Risks / Trade-offs

- 案件超过 20 条以后，比最新 20 条更早的只能靠案件类型缩小范围 → 模拟期很短，一只股票到不了很多；以后不够再加结束日
- 同一个成交日先卖后买时平均成本有偏差 → 记录只有累计，没有逐笔成交，影响很小
- 读取工具和交回工具同时给模型，三家模型上还没有试过 → 部署以后在服务器上用真实模型冒烟
- 查询结果可能很长，全部结论进上下文 → 条数上限可配置，费用不是约束；上线后看实际长度

## Migration Plan

启动时 Alembic 自动升级到 `v0015`。迁移只升级，不回退。服务器不用改配置。
