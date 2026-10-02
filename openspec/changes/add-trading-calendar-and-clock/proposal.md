# Proposal

## Why

M1 后面的变更（风控、订单与止损、紧急退出、降级）都要回答三类问题："现在是不是常规交易时段""下一个开盘是什么时候""某天之后第 N 个交易日是哪天"。目前代码里没有这些计算，也没有时间来源：Ruff 已经禁止直接读取系统时间，但还没有可以替代的 Clock。

美股的时间规则很容易算错：节假日和补休、13:00 收盘的半日市、夏令时让开盘时间在 13:30 和 14:30 UTC 之间切换，以及 UTC 日期和美东日期不是同一天。这些规则应该只在一处实现和测试，后面的变更直接使用。

本变更的目的只有一个：**提供全系统唯一的"现在"（Clock）和纽交所交易日历，让后续规则用交易日和 UTC 时间做确定性计算。**

引用的 Proposal（完整版）章节。这些章节规定了交易日和交易时段怎么用；本变更只提供它们需要的计算，规则本身由各自的变更实现：

- 「风控引擎与执行」的交易时段：只在常规交易时段下单；盘前盘后形成的方案等开盘执行，有效期要覆盖到下一个开盘
- 「订单生命周期」：券商不支持 GTC 时，止损单每个交易日开盘前重挂；每日收盘后对账
- 「持仓与退出」：紧急风险退出按盘中跌幅触发
- 「故障处理」：复查到期但因故障没有结论时，顺延到恢复后的第一个交易日
- 「触发」中的定时心跳和自定义跟进：每个交易日收盘检查；复查日默认 5 个交易日，范围 3 到 20 个；跟进最远 60 个交易日；每周最后一个交易日收盘后复盘
- 「记录、复盘与经验」中的案件记录：1、3、5、20、60 个交易日后的收益

## What Changes

- `shared` 新增 Clock 协议：`now()` 返回带时区的 UTC 时间
- `shared` 新增交易日历：
  - `TradingSession`：一个交易日，以及它常规时段的开盘和收盘时间（UTC）
  - `TradingCalendar`：用一段连续区间里的全部交易时段构造的纯计算对象，按日期和按时刻查询交易日。查询超出数据范围时报错，不返回错误的答案
- `integrations` 新增两个适配器：
  - 系统时钟：全项目唯一可以读取系统时间的文件，Ruff 只为它放行
  - 纽交所日历加载：用 `exchange_calendars` 读取 XNYS 的交易日，起止日期由调用方给出
- `tests/fakes` 新增 FakeClock，以及一份 2026 年 9 月到 2027 年 1 月的纽交所样例日历，供各模块的单元测试使用
- 加入第一个运行依赖 `exchange-calendars`
- `openspec/config.yaml` 的 `context`：
  - 补充约定：交易日和交易时段一律通过 TradingCalendar 计算，不把 UTC 时刻的日期当作交易日
  - "domain 可以接收 shared 中的协议（比如交易日历）"改为"对象或协议"，因为交易日历是纯计算对象，不是协议

## Non-goals

- 不实现任何使用日历的业务规则，比如交易时段检查、方案有效期、复查日校验。它们属于各自的变更
- 不提供等待。M2 接入 DBOS 后，等待改用它的持久化休眠
- 不计算结算日。现金账户的 T+1 按银行工作日算，不按交易日算：2026-10-12 哥伦布日、2026-11-11 退伍军人节，纽交所开市但不结算。可用的已结算资金在风控变更里处理
- 不手工补充临时休市。过去 25 年只有约 6 次，漏掉的后果有限（当天的订单被券商拒绝，心跳提前一天），M2 和 moomoo 核对交易日时会发现
- 不定义盘前、盘后和夜盘的时段边界：只在常规时段下单，没有规则依赖这些边界
- 不处理实时停市（全市场熔断、交易所故障），也不和 moomoo 的交易日接口核对，这些属于 M2
- 不定义案件侧的"每日"，比如每日模型预算、每天的跟进申请次数，到 M3 再定
- 不做 Decimal 数值类型（放在风控变更，它是第一个计算金额的变更）、ID 生成端口（和客户端订单 ID 一起放在订单变更）、回放时钟（M6）
- 不定配置文件的格式：加载范围由调用方传入，M2 接线时由 bootstrap 从配置读取

## Capabilities

### New Capabilities

- `clock`：全系统唯一的当前时间来源，返回 UTC 时间
- `trading-calendar`：纽交所交易日历：按日期和按时刻的查询、覆盖范围、从交易所日历数据加载

### Modified Capabilities

无。

## Impact

- **代码**：
  - 新增 `shared/clock.py`、`shared/trading_calendar.py`、`integrations/system_clock.py`、`integrations/nyse_calendar.py`
  - 新增 `tests/fakes/clock.py`、`tests/fakes/calendar.py`，以及镜像 `src/` 的测试目录 `tests/shared/`、`tests/integrations/`
- **依赖**：运行依赖 `exchange-calendars`，会带入 pandas、numpy、tzdata 等。只有 `integrations/nyse_calendar.py` import 它
- **配置**：`pyproject.toml` 的运行依赖、Ruff 的 `per-file-ignores`、mypy 对 `exchange_calendars` 的设置
- **文档**：`shared` 包的说明；`openspec/config.yaml` 的 `context`
- **对后续变更的影响**：
  - 读取"现在"一律用注入的 Clock；domain 不接收 Clock，"现在"作为参数传入
  - 交易日和交易时段一律查 TradingCalendar
  - 单元测试用 FakeClock 和样例日历

## 需要人决定

无。
