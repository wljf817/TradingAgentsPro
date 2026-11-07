# Design

## Context

原因见 proposal.md。`LlmClient.run` 已经能带读取工具、交回工具、工具次数上限和超时；`LlmBackend.sub_agent` 还抛 `NotImplementedError`。`AgentCallService.run_task` 把 `sub_agent` 交回的文本给 `TaskDispatchService.receive`，由 `_parse` 解析成信号或无信号。`run_text` 没有调用方，这个变更也不用它。

任务在 DBOS 子工作流里同时运行，每个任务读第 `version` 版快照；同一个案件第 2 版快照的条目 ID 都以 `s2/` 开头，`Snapshot.entry` 只查这一版。

模块边界：

- `contracts`：`agent_call.py` 新增 `SubAgentReply`（输出和工具返回过的内容的 ID）；新增 `sub_agent_context.py` 的 `SubAgentContext`
- `modules/gateway`：`GatewayConfig` 加 `max_sub_agent_tool_calls`；`ModelBackend.sub_agent` 改返回 `ModelReply[SubAgentReply]`；`domain/context.py` 加 `sub_agent_context`；`domain/result.py` 的 `received` 接已知的条目；`TaskDispatchService.receive` 改参数；去掉 `_parse`、`_location` 和 `Malformed`
- `integrations/llm`：新增 `sub_agent.py`（交回工具、工具返回过的内容的记录、注册的类型）；`LlmBackend.sub_agent` 组消息、调用 `LlmClient.run`
- `integrations/standin`：`sub_agent` 交回 `SubAgentReply`
- `workflows`：`CaseFlow.run_task` 写唤醒 agent，`case_entries.py` 加说明
- `bootstrap`：读两个角色 prompt，注册 News 和 Fundamentals

```
SubAgentRequest --> sub_agent_context --> 用户消息
宪章 + 角色 prompt -----------------------> instructions
注册的读取工具 + 这次运行的记录 ----------> tools

LlmClient.run --> Signal | NoSignal --------+
              \-> InvalidOutput --> InvalidReply --+
                                                   v
                     SubAgentReply(output, tool_entries)
                                                   |
run_task：已知的条目 = 快照的条目 + tool_entries   |
                                                   v
                     TaskDispatchService.receive --> received --> Result
```

## Goals / Non-Goals

**Goals:**

- News 和 Fundamentals 的模型改成真实厂商就能调用，骨架不再有它们的 `NotImplementedError`
- 后面四个工具变更只注册工具，不用再改调用和校验

**Non-Goals:**

- 见 proposal.md 的 Non-goals

## Decisions

### D1 输出是类型化的回复，不再是 JSON 文本

`sub_agent` 交回 `SubAgentReply`：`output` 是 `Signal | NoSignal | InvalidReply`，`tool_entries` 是工具返回过的内容的 ID。格式由交回工具的参数校验，和 PM 一样，所以网关的 `_parse` 不再需要。

**不保留文本接口：** 把模型输出转成 JSON 交给网关再解析一次，校验做两遍；文本里也放不下工具返回过的内容。JSON 文本是替身时代的接口，现在只有替身和测试在用。

`InvalidReply` 复用 PM 的，`text` 不进 Result：Result 的说明是 `reason`，够定位。`LlmBackend` 把接住 `InvalidOutput` 的几行抽成一个函数，PM 和子 agent 共用。

### D2 引用校验放在网关，已知的条目由 `run_task` 组出

`run_task` 用快照的全部条目 ID 加上 `tool_entries` 组出已知的条目，交给 `received`。检查写成 `domain/signal.py` 的 `unknown_entries(output, known)`，在 `signal_rejection` 之后调用；不放进 `signal_rejection`，因为它也用于 PM 的结论。

这个失败的说明要带 ID，以后看失败率时才知道错在哪，所以不走 `SignalRejection` 枚举，直接写文字；`result_text` 对不在映射里的说明原样写出，不用改。

**已知的条目用整版快照，不用子 agent 看到的那部分：** 这是 Proposal 的写法。模型只知道看到的 ID，编出一个刚好存在的 ID 几乎不可能，多收窄没有实际意义，还要多一份集合。

### D3 工具返回过的内容用一个记录对象

注册的是一个函数：收到 `SubAgentRequest` 和一个记录函数，给出这次运行的读取工具。工具把自己返回的内容的 ID 交给记录函数。`LlmBackend.sub_agent` 每次运行新建一个集合，所以重试和换模型自然重新开始，调用错误时集合直接丢掉。

**不解析工具结果取 ID：** 工具结果的格式各不相同，让工具自己报更简单。

ID 的具体写法由各工具变更定，规则只有一条：不带 `s<版本>/` 前缀，不和快照条目相同。不带版本是因为这些内容不属于某一版快照。

### D4 注册表放进 `LlmBackend`，Quant 不注册

`LlmBackend` 收到 `Mapping[SubAgent, SubAgentSetup]`，`SubAgentSetup` 是系统指令和给出读取工具的函数。`bootstrap` 注册 News 和 Fundamentals，读取工具都是空的。

查不到时抛 `NotImplementedError("Quant 还没有接上真实模型")`，和 Critic、Reviewer 现在的写法一致。Quant 是两次模型调用的固定工作流，不走这条路。

工具函数现在都是空的，这是 Proposal 已经定下要接的四个工具，不算假想的扩展点。

### D5 上下文放在 `question`

子 agent 只有一条消息，直接作为 `question` 传给 `run`，`history` 为空。PM 把上下文放进 `history` 是因为要接对话，子 agent 没有对话。

`SubAgentContext.inputs` 用 `SerializeAsAny[SnapshotEntry]`：字段声明成基类时 Pydantic 只序列化基类的字段，会丢掉条目的内容。

输入里写的条目原样给，包括 PM 自己写进去的账户或股票池：那是发起者明确要交给它看的。

### D6 唤醒 agent 由 `CaseFlow.run_task` 写

Proposal 要求每次调用记宪章版本和快照版本。生成 Result 的条目写在 `TaskDispatchService`，它拿不到这两个版本；`CaseFlow` 两个都有，PM 的唤醒日志也在这里。`run_task` 先读 `tasks.record(task_id)`，已经有 Result 时直接返回，不写条目。

耗时、费用和模型已经在生成 Result 里，这条不重复写，只写子 agent、任务 ID 和两个版本。

### D7 工具次数放在网关配置

`max_sub_agent_tool_calls` 放进 `GatewayConfig`，和 `max_history_queries` 一样进配置文件的 `gateway` 一节。没有读取工具时也照常传给 `run`，只影响请求次数的上限，没有害处。

### D8 `run_text` 不动

删掉它要把 model-adapter spec「运行」「读取工具」「工具次数上限」里全部用文字收尾的场景都改写，和这个变更的目的无关。

## 省略的情况

- 模型在一个回答里同时调用读取工具和交回工具：已有规则，读取工具照样执行，它返回的内容算已知
- 子 agent 的上下文里写评估窗口的选项：写进宪章，属于「写 prompt」
- 输入里的新闻或公告很长：原样给，PM 选的内容不截断
- 新闻内容里夹带指令：子 agent 只有读取工具和交回工具，纪律写进 prompt，代码不过滤
- 崩溃发生在任务有了 Result 和写唤醒 agent 之间：少一条唤醒 agent，不补
- 记录函数同时被调用：同一个回答里的几次工具调用在一个事件循环和线程池里执行，集合的 `update` 在 CPython 下是原子的，不加锁

## Risks / Trade-offs

- [引用抄错一个字符整个任务就失败，还占 PM 一个任务数] → 已确认先按 Proposal 做；说明写明错的 ID，上线后看失败率再定是否打回一次
- [News 和 Fundamentals 配成真实模型时没有数据工具，只能答无信号或者瞎猜] → 线上保持替身，等工具变更和「写 prompt」
- [只测过一次两个模型的工具调用] → 主机冒烟用假工具再跑 deepseek-flash 和 Haiku 5.5，确认两个交回工具都被接受
- [子工作流崩溃重做时重新调用模型] → 已有行为，有 Result 的不再调用，费用可能多记一次

## Migration Plan

没有迁移。部署以后模型文件还是替身，程序日志多一种唤醒 agent，其他行为不变。回滚就是回到上一个提交。

## Open Questions

无。
