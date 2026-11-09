# Proposal

## Why

行情模块已经能按一个 Quant 任务算出量化指标，但还没有人用：Quant 的任务运行时不算数，PM 和 Critic 也看不到这些数字，定止损时没有 ATR 和关键价位可用。这个变更让 Agent 网关在调用 Quant 的模型以前，由代码按 Task 和快照算出量化指标，存成可以引用的条目，交给 Quant，并随 Result 交回给 PM 和 Critic。

## What Changes

- Quant 的任务在调用模型以前，Agent 网关按 Task 和这次运行读的快照算出量化指标（quant-indicators spec），存成条目 `quant/<task_id>`；同一个任务只算一次，重做这一步时用存下的
- 算量化指标的请求由 Task 和快照给出：股票和周期范围取 Task；板块 ETF 取快照的股票池里这只股票的板块，不在股票池时取粗筛名单里的板块，都没有时只比 SPY；最新价和量比取快照的行情；事件取 Task 的输入里在这版快照中查到的事件条目
- K 线取不到时，任务不调用模型，直接失败；失败的原因多一种「数据取不到」
- 调用 Quant 时一并交给它这个条目；它引用这个条目合法
- PM 和 Critic 拿到的每个 Task 和 Result 旁边，Quant 的任务带上它的量化指标条目：PM 派发任务的工具结果里就有
- Quant 的替身引用这个条目，所以模型还是替身时，K 线和量化指标这条路在服务器上就跑起来
- 快照的行情条目加上量比和成交量（行情源给出的；开盘后还没有成交时没有）
- 新增量化指标表 gateway_indicators，迁移 v0019
- 启动时装配 Alpaca 的 K 线适配器、K 线和量化指标的用例，接进 Agent 网关

引用完整版 Proposal 的章节：「Agent」的「Quant」（流程第 1 步、代码算的数字的最后一段）、「News」和「Fundamentals」的输入、「Task / Result 通信」、「共享上下文」里子 agent 看到的内容。

**Non-goals：**

- 真实模型的 Quant：两次调用、市场状态、规则文件、把 K 线交给模型，都在「Quant 调用」；这个变更以后向真实模型调用 Quant 仍然报错
- Task 的输入在别的版本的快照、`news/`、`filing/` 条目里查不到的问题：拆 Critic 时一起修；这里事件也只在这版快照里找
- Critic 的上下文怎样摆放量化指标：Critic 还没有接上真实模型，它的输入里已经有这些条目，摆放在拆 Critic 时定
- prompt 里怎样告诉 PM 要在事件反应时把事件写进输入：「写 prompt」

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `task-dispatch`：新增「Quant 任务的量化指标」（请求怎样由 Task 和快照给出、条目 ID、只算一次、存储）；「Result 的格式」多一种失败的原因「数据取不到」
- `agent-calls`：「每个 agent 的输入和输出」Quant 和 Critic 的输入带量化指标条目；「对话的每一轮」交回的 Task 和 Result 带条目；「子 agent 的任务」运行 Quant 的任务先取得条目，取不到 K 线时失败、不调用模型
- `pm-calls`：「对话变成消息」`dispatch_tasks` 的工具结果带量化指标条目
- `signal-format`：「子 agent 引用的条目」已知的条目加上这个 Quant 任务的量化指标条目
- `sub-agent-calls`：「子 agent 的上下文」的行情带量比和成交量
- `stand-ins`：「子 agent 替身」Quant 的替身引用量化指标条目
- `state-snapshot`：「共享事实」行情条目加上量比和成交量
- `state-store`：「表的归属和迁移」加上量化指标表
- `bootstrap`：「模拟盘的装配」装配 K 线和量化指标，接进 Agent 网关

## Impact

- 代码：`contracts`（量化指标条目、`TaskOutcome` 和 `SubAgentRequest` 带条目、失败的原因、行情条目的两项）、`modules/gateway`（运行任务前取得条目、存储、端口）、`workflows`（由 Task 和快照组出量化指标的请求，满足网关的端口；案件工作流交回带条目的结果）、`workflows/snapshot_flow.py`（行情条目的两项）、`integrations/standin`（Quant 的替身）、`bootstrap`（装配、迁移）
- 迁移：v0019 新建 gateway_indicators
- 测试：`tests/modules/gateway`、`tests/workflows`、`tests/integrations/standin`、`tests/bootstrap`；全程序的测试换上 K 线源的替身
- 配置：没有新的配置项；量化指标和 K 线的配置已经在 `marketdata` 一节
- 部署：带迁移；部署以后替身 PM 开出开仓方案或者遇到要 Quant 的事件时，就会向 Alpaca 取 K 线
- 文档：完整版 Proposal「Task / Result 通信」「News」「Fundamentals」和「共享上下文」里子 agent 看到的「最新价和前收盘价」改成带量比和成交量；两份路线表把指标条目改成进行中，类型改成实际修改的 spec

## 需要人决定

无。量化指标存成条目、PM 随 Result 看到、替身 Quant 引用它、事件取 Task 的输入、快照的行情加上量比和成交量、K 线取不到时任务失败不调用模型，2026-11-09 拆分 Quant 时已经和人确认。板块 ETF 改为只从快照里找（拆分时说的是不在股票池时问公司数据），理由见 design D3。
