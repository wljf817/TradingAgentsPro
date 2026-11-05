# Design

## Context

原因见 proposal.md。快照由 `workflows/snapshot_flow.py` 组成：账户、股票池、行情读各模块，市场状态现在只取交易日历。行情在生成快照时直接向行情模块取，不存表；读不到时整份快照报错。

模块边界：`contracts.snapshot` 加 `VixClose`、`ScheduleItem`、`ScheduleKind`，`MarketEntry` 带上它们；`modules/marketdata` 定义 VIX 源端口、日程规则和市场背景服务 `MarketContextService`，用这些 contracts 的模型；`integrations/cboe` 实现端口；`workflows/snapshot_flow` 调用行情模块；`bootstrap` 装配。

## Goals / Non-Goals

**Goals:**

- 每份快照的市场状态都能看到最近的 VIX 收盘值和接下来的重要日程

**Non-Goals:**

- 见 proposal.md 的 Non-goals

## Decisions

### D1：VIX 生成快照时读，不存表

和行情一样在生成快照时取一次，取到的值随快照存下，回放读的是快照。不加表、不加迁移。文件约 470 KB，一天几个案件，请求量可以忽略。

没有采用的做法：收盘心跳写进新表。要多一张表和一次迁移，换来的只是少几次请求。

### D2：VIX 读不到时快照照常生成

行情读不到时快照报错，是因为方案要用价格。VIX 只是背景信息，Cboe 这个外部文件不该卡住案件，尤其是持仓风险事件。读不到时这一项为空，写一条 WARNING。

### D3：取日期早于快照交易日的最近一行

快照的交易日盘前和盘中是当天，收盘以后是下一个交易日。取日期早于它的最近一行，不会读到当天还没收盘的值，收盘以后也能读到当天的值。实测 Cboe 的文件在收盘后几小时还没有当天这一行，这时取到的是上一行，日期随值一起给出，模型知道它是哪一天的。

### D4：日程写在配置里

FOMC 的日期每年公布，CPI 和非农的日期也提前公布，一年 30 多个日期，写进 `MarketDataConfig` 的默认值，用 Pydantic 校验。回放时日程不随时间变，也没有运行时依赖。

没有采用的做法：解析美联储页面（BLS 的页面拒绝脚本访问，只解决一半）；申请 FRED 的 key（多一个外部依赖和环境变量）。

### D5：VIXY 只加在模拟盘的快照配置里

快照配置的默认指数不变，装配时传入 SPY、QQQ、VIXY。这样不用改 state-snapshot spec 里按默认配置数条目的场景。VIXY 由 moomoo 行情提供，是实时的。

### D6：`MarketEntry` 的新字段带默认值

存着的旧快照没有这两项，字段默认为空，读回不报错。

## 省略的情况

- 配置里的日期用完：`schedule` 为空，不告警，日期要在 2027 年底以前续上
- BLS 临时改期（比如政府停摆）：不处理，手动改配置
- Cboe 的文件很旧：不判断新旧，日期随值给出

## Risks / Trade-offs

- [配置的日期打错] → PM 看到错的日程；日期来自官方日历，改配置就能修正
- [Cboe 改了地址或列名] → 读不到，快照没有 VIX，日志有 WARNING；冒烟测试能发现

## Migration Plan

归档并提交以后，按 `deploy/README.zh.md` 的「更新」步骤更新服务器，没有数据库迁移，不加环境变量。回滚就是退回上一个提交。
