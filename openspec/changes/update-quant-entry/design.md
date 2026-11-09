# Design

## Context

行情模块的 `IndicatorService.indicators(IndicatorRequest) -> QuantIndicators` 已经能按一个 Quant 任务算出全部数字（quant-indicators spec），K 线取不到时抛 `BarsUnavailableError`；还没有装配，也没有调用方。

Agent 网关的 `AgentCallService.run_task(task_id, snapshot)` 运行一个任务：已经有 Result 就直接给出；否则按备用模型的步骤调用 `ModelBackend.sub_agent(model, timeout, SubAgentRequest(task, snapshot))`，拿到输出后把「快照的全部条目 + 工具返回过的 ID」当作已知的条目交给 `TaskDispatchService.receive`。Task 和 Result 存在 gateway_tasks、gateway_results，`TaskRecord(task, result)` 是读回的单位。

PM 和 Critic 看到的任务结果是 `TaskOutcome(task, result)`：案件工作流在派发的子工作流都结束以后，按请求的顺序从网关读 `TaskRecord` 拼出来（`CaseFlow.outcomes`）；真实 PM 的后端把每一轮交回的结果 `model_dump_json()` 当工具结果（`integrations/llm/pm.py`），Critic 的输入 `CriticRequest.research` 也是 `TaskOutcome`。

网关读别的模块的数据已有先例：`HistoryReader` 是网关的端口，由 `workflows/case_history.py` 读案件记录和执行模块来满足。

快照的 `QuoteEntry` 现在只有最新价、前收盘价和更新时刻；行情模块的 `Quote` 已经有 moomoo 的量比和成交量。股票池记录和粗筛名单里的板块都是 SPDR 板块 ETF 的代码（company-data spec「板块」），快照里有案件股票的板块 ETF 的行情。

## Goals / Non-Goals

**Goals:**

- 一个 Quant 任务只算一次数字，模型的每次尝试、崩溃以后的重做、PM 和 Critic 看到的都是同一份
- 数字只由快照和 Task 决定：回放同一个案件时组出同样的请求
- 模型还是替身时，K 线和量化指标在服务器上照样跑

**Non-Goals:**

- 见 proposal 的 Non-goals

## Decisions

### D1：模块边界

| 位置 | 内容 |
| --- | --- |
| `contracts/quant.py` | `QuantEntry(entry_id, indicators: QuantIndicators)`；`quant_entry_id(task_id) -> str` 给出 `quant/<task_id>` |
| `contracts/task.py` | `TaskFailure.DATA_UNAVAILABLE`（数据取不到） |
| `contracts/agent_call.py` | `TaskOutcome.quant: QuantEntry \| None = None`；`SubAgentRequest.quant: QuantEntry \| None = None` |
| `contracts/snapshot.py` | `QuoteEntry.volume_ratio: Decimal \| None = None`、`volume: int \| None = None`；默认值给存着的旧快照 |
| `modules/gateway/ports.py` | `QuantNumbers` 协议：`indicators(task, snapshot) -> QuantIndicators`，K 线取不到时抛同在 ports 的 `QuantDataUnavailableError`；`TaskStore` 加 `add_quant(task_id, entry)`，`record` 和 `case_records` 读回的 `TaskRecord` 带上条目 |
| `modules/gateway/domain` | `TaskRecord.quant`；`result.py` 加数据取不到的 Result，和调用失败一样晚于截止时间时是超时 |
| `modules/gateway/repo.py` | 量化指标表 gateway_indicators：task_id 主键、data JSONB；读任务时和 Result 一样按 task_id 连上 |
| `modules/gateway/service.py` | `TaskDispatchService.add_quant`、`unavailable`；`AgentCallService` 多一个 `QuantNumbers`，`run_task` 见 D2 |
| `workflows/quant_numbers.py` | `indicator_request(task, snapshot) -> IndicatorRequest`（纯函数，规则见 task-dispatch spec「Quant 任务的量化指标」）；`SnapshotIndicators(indicators: IndicatorService)` 满足 `QuantNumbers`，把 `BarsUnavailableError` 换成 `QuantDataUnavailableError` |
| `workflows/case_flow.py` | `_outcome` 带上 `record.quant` |
| `workflows/snapshot_flow.py` | `_quote` 抄上量比和成交量 |
| `integrations/standin/backend.py` | Quant 的替身引用 `request.quant.entry_id` |
| `bootstrap` | `Externals.bar_history`；装配见 D7；迁移 v0019 |

模块之间传的 Pydantic 模型：`Task`、`Snapshot` 从网关交给 `QuantNumbers`，`QuantIndicators` 交回；`QuantEntry` 随 `SubAgentRequest` 交给后端，随 `TaskOutcome` 交给案件工作流。

### D2：网关在 `run_task` 里取得条目

`run_task` 的顺序：已经有 Result → 直接给出；目标是 Quant → 读存下的条目，没有就经 `QuantNumbers` 算、经 `TaskDispatchService.add_quant` 存下；`QuantDataUnavailableError` → `TaskDispatchService.unavailable(task_id, detail)`，不调用模型；然后照旧调用，`SubAgentRequest` 带上条目，已知的条目加上它的 ID。

放在网关而不是案件工作流：「只算一次」「先存再调用」「已知的条目加上它」都和 `run_task` 已有的「有 Result 就不调用」「拼已知的条目」在同一处，Result 也只由网关写。组请求要读快照、要用行情模块的类型，照 `HistoryReader` 的做法由 workflows 满足端口，网关不 import 行情模块。

另一种是案件工作流先算好再把条目交给 `run_task`：重做时要另外判断有没有存过，失败的 Result 也要由工作流来写，逻辑分在两处。

### D3：板块 ETF 只从快照里找

拆分时说的是不在股票池时问公司数据（Finnhub 的行业）。改为只看快照：股票池里有就用它的板块，否则用粗筛名单里的，都没有就只比 SPY。

- 快照是案件读事实的唯一来源，回放时不会因为 Finnhub 改了行业而算出不同的数字
- 每个任务少一次 Finnhub 请求，也少一种失败
- 都没有的只有寻股里 PM 指定、又不在粗筛名单里的股票，少见；这时只比 SPY 是 quant-indicators spec 已有的情况

### D4：条目单独一张表

条目在调用模型以前就要有，崩溃重做时要读回来，所以不能等到 Result 才写进 gateway_results；Task 写入以后不能改，也不能放进 gateway_tasks。单独一张表，主键 task_id，只追加；读任务时和 Result 一样按 task_id 连上，`case_records` 不用另查。内容是 `QuantIndicators` 的 JSON：小数在 JSON 里是字符串，读回的每一位都和写入的相同。

同一个任务写第二次按主键冲突报错：一个任务只在一个 DBOS 子工作流里运行，重做时先读到存下的，不会写第二次。

### D5：失败的原因单独一种

`CALL_FAILED` 的含义是模型都不可用（model-fallback spec），M6 按它看模型的可靠性；K 线取不到和模型无关，混进去会算错。新的原因 `DATA_UNAVAILABLE` 没有模型、费用 0；晚于截止时间时和调用失败一样记超时，用同一个判断。

### D6：条目 ID 不带快照版本

条目属于任务，不属于哪一版快照，所以写成 `quant/<task_id>`，和工具返回的 `news/…`、`financials/…` 一样不带 `s<版本>/`。已知的条目只加这个任务自己的：信号说的是这次算出来的数字，引用别的 Quant 任务的条目按未知的条目处理。

PM 的结论里的信号不检查引用（signal-format spec），PM 引用 `quant/…` 不用改校验。

### D7：PM 和 Critic 怎样看到

`TaskOutcome` 多一个 `quant`，PM 的后端把交回的结果整个转成 JSON，所以工具结果里自然有条目，`integrations/llm` 不用改；没有条目的写成 `null`，就是「写明没有」。Critic 的 `research` 和它自己的补充研究也是 `TaskOutcome`，一并带上。一个条目的 JSON 大约 3 到 4 千字符（周线、三条均线、各 3 个价位和缺口、两个事件），一次派发最多几个 Quant 任务，不会撑大上下文。

### D8：装配

| 部件 | 装配 |
| --- | --- |
| K 线源 | `Externals.bar_history(settings.alpaca_bars)`，运行时是 `AlpacaBarHistory`，key 和模拟盘的是同一组；测试换成 `tests/fakes/bars.py` |
| K 线 | `BarService(bar_history, calendar, settings.marketdata)` |
| 量化指标 | `IndicatorService(bars, calendar, settings.marketdata)` |
| 网关 | `AgentCallService(..., SnapshotIndicators(indicators))` |

K 线源放进 `Externals`：全程序的测试里替身 PM 一开仓就会派 Quant 的任务，不能去连 Alpaca。

### 省略的情况

- Task 的输入里别的版本的事件条目（比如 Critic 的任务读第 2 版、输入写 `s1/event/…`）找不到，不算进事件：和子 agent 的上下文一样，拆 Critic 时一起修
- Alpaca 的 4xx（key、权限、订阅）原样报错，这一步的子工作流出错，和别的程序错误一样
- 部署时有案件正在运行：DBOS 记下的旧 `TaskOutcome` 没有 `quant`，恢复时可能读不出来；照以前的做法在没有案件运行时部署
- Quant 的替身不看 Task 的输入，只引用条目

## Risks / Trade-offs

- [每个 Quant 任务多 1 到 7 秒取 K 线（服务器上 60 分钟线 20 天最慢，6.7 秒）] → 在任务的 10 分钟时限里，模型尝试的超时不变
- [Alpaca 断线时 Quant 的任务直接失败] → 适配器按 alpaca-bars spec 不重试；PM 下一轮可以再派（失败的不复用），服务器上的短暂断线一天几次，而且断线时降级模式本来就暂停开仓
- [替身 PM 在服务器上开仓时会真的取 K 线] → 这正是目的；取不到时开仓方案可能因为研究不足被退回，和真实模型一样

## Migration Plan

- 迁移 v0019 只新建 gateway_indicators，空表，不动已有数据
- 部署：提交以后照常更新服务器，迁移在启动时跑；不用改配置文件
