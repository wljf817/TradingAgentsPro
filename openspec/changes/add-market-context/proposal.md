# Proposal

## Why

完整版 Proposal 的 Shared State 写了市场状态包含波动率和重要日程，但快照的市场状态现在只有交易日和交易时段，再加 SPY、QQQ 的行情。接上真实的 PM 以后，它判断大环境时看不到恐慌程度，也不知道明天是不是议息或 CPI 日。这个变更先把事实补进快照，后面的 PM 和 Critic 直接读。

## What Changes

- 快照的市场状态加上最近一个 VIX 收盘值：数据来自 Cboe 免费的历史文件，生成快照时读一次，读不到时没有这一项，其他照常
- 快照的市场状态加上接下来的重要日程：FOMC 决议、CPI、非农的日期写在配置里，默认列出之后 10 个交易日内的
- 模拟盘的快照配置把 VIXY 加进指数，看盘中的波动变化
- 行情模块加「VIX 收盘值」和「重要日程」两项，Cboe 适配器放在 integrations

## Capabilities

### New Capabilities

- `market-context`：VIX 收盘值、Cboe 数据源和重要日程

### Modified Capabilities

- `state-snapshot`：「共享事实」的市场状态加 VIX 收盘值和重要日程
- `bootstrap`：「模拟盘的装配」的行情和快照两行

## Impact

- 代码：`contracts/snapshot.py`、`modules/marketdata`、新增 `integrations/cboe`、`workflows/snapshot_flow.py`、`bootstrap/app.py`
- 数据：没有迁移；存着的旧快照没有这两项，读回时为空
- 依赖：不加新依赖，用 httpx 读 Cboe 的 CSV，不需要 key
- 部署：不加环境变量

## 引用的 Proposal 章节

- 完整版「共享上下文」的 Shared State：市场状态包含日期、交易时段、指数、波动率、重要日程
- 完整版「技术栈与部署」的数据源一行

## Non-goals

- 盘中实时的 VIX：Cboe 的文件只有收盘值，moomoo 能否给 VIX 没有验证；盘中看 VIXY
- GDP、PCE、财报季、期权到期日：先只放影响最大的三种
- 自动抓取日程：Finnhub 的经济日历免费 key 没有权限，BLS 的页面拒绝脚本访问，日期写在配置里
- PM 和 Critic 怎么使用这些事实：属于接入 PM 的变更

## 需要人决定

无
