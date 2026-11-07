# Design

## Context

`LlmClient._attempt` 已经用 `capture_run_messages` 捕获一次尝试的消息，只用来算用量和取不合法的原文，用完就丢。`AgentCallService._call` 是 PM、Critic、Reviewer 和子 agent 的唯一入口，每次尝试都经过它，费用也在这里记。`modules` 不能 import PydanticAI。

2026-11-06 用 PydanticAI 2.66 实测：捕获的消息带系统指令，`question=None` 时指令在最后一个请求上；调用出错和超时时至少有发出的请求；`ModelMessagesTypeAdapter` 导出的 JSON 能还原，文字里的 NUL 被转义成 `\u0000`。

## Goals / Non-Goals

**Goals:**

- 真实模型的每次尝试都有完整记录，失败的也有
- 适配器只交出文字，网关不需要懂 PydanticAI 的结构

**Non-Goals:**

- 读记录的业务逻辑留给 M6，这里只有存储的按案件读回
- 替身不产生记录

## Decisions

### D1：单独的表 `gateway_model_calls`

一条记录可以到几 MB，程序日志是一行一个动作，启动和巡检还要查最新的条目，全文放进去不合适。表由 Agent 网关写入，符合「每张表只由一个模块写入」。备选是写进 `programlog_entries.detail`，放弃。

列：`id`、`case_id`（建索引）、`task_id`、`agent`、`model_vendor`、`model_name`、`served_model`、`snapshot_version`、`started_at`、`ended_at`、`input_tokens`、`output_tokens`、`error`、`transcript`。

### D2：`transcript` 用 Text，不用 JSONB

JSONB 拒收 `\u0000`，新闻和公告的正文里可能有。Text 存导出的 JSON，转义以后不含 NUL，内容也和导出的完全一致；要查时再临时转换。

### D3：每次尝试存全部消息，包括之前的对话

PM 后面的尝试会重复前面的对话，但一条记录自己就是完整输入，不用拼接，也和「完整输入」的要求一致。PostgreSQL 会自动压缩大文字。

估算：一个案件约 0.5 到 1 MB（PM 几次调用，子 agent 几个任务，每个最多约 100k 字），每天 8 个案件，一年约 2 到 3 GB。主机还有 136 GB。

### D4：消息在网关眼里是一段文字

`LlmClient` 用 `ModelMessagesTypeAdapter.dump_json` 导出。网关只存不解析，所以 `modules` 不碰 PydanticAI，M6 要还原时放在 `integrations/llm`。备选是定义自己的消息模型，多一层转换，没有用处。

### D5：沿现有的返回路径带出消息

| 类型 | 新增项 | 来源 |
| --- | --- | --- |
| `ModelReply`（gateway ports） | `transcript: str \| None = None` | `_attempt`；`_require_tool_output` 原样传下去 |
| `ModelCallError`（gateway ports） | `transcript: str \| None = None` | `_attempt` 的三个 except |
| `InvalidOutput`（llm errors） | `transcript: str` | `_invalid_output` 和 `_require_tool_output`；`LlmBackend` 转成 `ModelReply` 时带上 |

替身不写这一项，是 `None`，网关看到 `None` 不写记录，不用单独判断是不是替身。

### D6：网关的写入点

`modules/gateway/domain/` 加不可变的 `ModelCallRecord`。`ports.py` 加 `ModelCallStore`（`add`、`case_calls`），`repo.py` 加 `PostgresModelCallStore`。`AgentCallService` 注入存储和 `Clock`。

`_call` 里每次 `attempt` 前读开始时刻，之后先 `_charge`，再写记录。`_call` 加可选的 `task_id`，由 `run_task` 传入。模块之间传的 Pydantic 模型不变，`ModelCallRecord` 不出网关。

### D7：写入出错时往上抛

和 `_charge` 相同，不捕获。此时数据库已经有问题，费用和 Result 也写不了。

**省略的情况：**

- 进程在模型回答以后、写入以前崩溃：这次尝试没有记录，费用也没有
- 超时那一刻正在执行的工具：没有结果，不在消息里
- `ask` 和 `run_text` 没有调用方，消息照样带出，不单独测网关路径

## Risks / Trade-offs

- [数据库和备份变大] → 按 D3 估算一年几 GB；上线后看实际增长
- [导出格式跟着 PydanticAI 版本变] → JSON 本身可读，升级时还原出错再处理
- [主机不能保存冒烟的记录] → 表的读写用本地数据库测，主机冒烟只检查三个真实模型给出的消息

## Migration Plan

迁移 v0016 建表，启动时自动运行，`deploy/` 不用改；只升级不回退。替身期间表一直是空的。
