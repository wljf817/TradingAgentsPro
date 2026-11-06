# Design

## Context

原因见 proposal.md。`LlmClient.run` 已经能带交回工具、之前的对话和超时，`pm_context` 和系统宪章已经有了，`LlmBackend.pm`、`pm_respond` 还抛 `NotImplementedError`。`LlmClient` 用 `InvalidOutput` 报告输出不合法，子 agent 的文本输出碰不到它；`AgentCallService._call` 只接 `ModelCallError`。

对话在工作流的 `case.pm_turns` 里，每轮是 `PmTurn(reply, feedback)`，调用 PM 时全部带上，所以模型的历史由代码重建，切到备用模型也能接着用。

模块边界：

- `contracts`：新增 `InvalidReply`（原文、原因）；`PmReply` 加上它；新增 `PmResponseReply = PlanResponse | InvalidReply`；`PmTurn.reply` 可以是 `InvalidReply`，这时 `feedback` 必须是 `Returned`
- `integrations/llm`：`LlmBackend` 组消息、调用 `LlmClient.run`，把 `InvalidOutput` 换成 `InvalidReply`；`LlmClient` 的问题可以为 `None`，`InvalidOutput` 带原因，`ModelReply` 带模型名
- `modules/gateway`：`ModelReply`、`Answered` 加模型名，`ModelBackend.pm_respond` 改返回类型；重试和切换的逻辑不变
- `modules/scheduler/domain/stage.py`：新增事件 `reject_response`
- `workflows`：研究和回应处理 `InvalidReply`；唤醒日志写模型名和输出不合法
- `bootstrap`：读角色 prompt，把宪章和它交给 `LlmBackend`

```
PmRequest --> pm_context --> 第一条消息 --+
PmRequest.turns --> 每一轮的消息 ---------+--> history
宪章 + 角色 prompt -----------------------> instructions

LlmClient.run(question=None) --> ModelReply[DispatchTasks | SubmitConclusion | PlanResponse]
                             \-> InvalidOutput --> ModelReply[InvalidReply]
```

## Goals / Non-Goals

**Goals:**

- PM 的模型改成真实厂商就能调用，骨架不再有 PM 的 `NotImplementedError`
- 输出不合法走已有的退回重交，不多一条路

**Non-Goals:**

- 见 proposal.md 的 Non-goals

## Decisions

### D1 输出不合法是一种回复，不是异常

`LlmBackend` 接住 `InvalidOutput`，交回 `ModelReply(InvalidReply(...), usage)`。网关看到的是一次成功的尝试，费用、模型状态和实际使用的模型都不用改，这正是 model-fallback spec 要的结果：格式不合法不算不可用，不切换模型。

**不在网关接异常：** 网关不能 import `integrations`，要在 ports 里再定义一个异常和一条分支，还要给 `Answered` 加一种情况。`InvalidReply` 放进 contracts，也能直接进 `PmTurn`，作为一轮留在对话里。

### D2 上下文放进 history 的第一条，`question` 为 `None`

`add-shared-context` 的 D3 把上下文放在 `question`，这是错的：`agent.run(question, message_history=history)` 把问题排在历史后面。实测（2026-11-06，FunctionModel）：

| 传法 | 模型收到的 |
| --- | --- |
| `question` 是上下文，`history` 是对话 | 工具调用、工具结果和上下文 |
| `question` 为 `None`，`history` 是上下文和对话 | 上下文、工具调用、工具结果 |
| `question` 为 `None`，`history` 只有上下文 | 上下文 |

所以 PM 的每次调用都走第二、三行，第一次调用不用分支。`LlmClient.run` 的 `question` 改成 `str | None`，`ask` 和 `run_text` 不变。

**不写一句固定的「请继续」当问题：** 这句话是 prompt 内容，而且模型什么都不差，不需要它。

### D3 对话到消息的对应

规则在 pm-calls spec「对话变成消息」。工具调用的 ID 由轮次的序号生成，厂商只要求配对。工具结果和第一条消息都用 `model_dump_json()`。

不合法的那一轮写成模型的文字和用户消息，不是工具调用：它的参数本来就不合法，工具调用还可能根本没有。

### D4 工具的名字和说明

研究有 `dispatch_tasks`、`submit_conclusion`，回应只有 `respond_to_challenge`，这样研究时不能交回回应，回应时不能改结论。回应时再调研究的工具算调用不存在的工具，就是输出不合法。

工具说明只写一句事实，比如"派发任务"，不算 prompt，要改写时在「写 prompt」里改。这个变更没有读取工具，`tool_limit` 是 0。

### D5 退回和重交次数

研究阶段的 `InvalidReply` 调用已有的 `reject_conclusion`，转换完全相同。回应阶段新增 `reject_response`：次数没用完留在 PM 回应，用完调用 `fail()`。

回应阶段用完不算结论无效：结论已经通过并且记下了，`CaseEndReason.INVALID_CONCLUSION` 的含义是没有结论。这和 PM 回应时调用失败的结果一样，都是"有结论，方案不执行"。

次数全案件共用一个字段，不给回应单开：多一个配置项解决不了实际问题，真模型连续不合法时该看的是 prompt。

### D6 原因取自校验错误

`_attempt` 接到 `UnexpectedModelBehavior` 时，`error.__cause__` 是 `ToolRetryError`，它的文字逐条列出字段和错误（实测，2026-11-06）；`UsageLimitExceeded` 没有 cause，用它自己的文字。`InvalidOutput` 加 `reason`，只回文字时原因是固定的一句。

**不开 PydanticAI 的输出重试：** 它能在一次运行里自动重试，但那样重试不进对话、不算重交，也不写日志。

### D7 空回答写成固定文字

厂商拒收空的文字块，而模型确实会给空回答（`update-model-tool-loop` 实测过）。`InvalidReply.text` 用 `error.text.strip() or "（空回答）"`，放在 `LlmBackend` 里。

### D8 角色 prompt 是一个 Markdown 文件

`bootstrap/pm_prompt.md`，随代码发布，启动时读入。文件没有版本：程序日志记的是宪章版本，prompt 的历史在 git 里。`LlmBackend(client, pm_instructions)` 只收拼好的字符串，Critic 和子 agent 接入时再加自己的。

`_case_side` 里 `load_charter` 排到 `_model_backend` 前面。

### D9 模型名只写日志

`ModelReply` 和 `Answered` 加 `served_model: str | None`，取 `ModelResponse.model_name`，替身没有。DeepSeek 的名字不固定版本，厂商回答里写的才是当时的模型，但它是否比请求的名字更细要看厂商。

`add-model-adapter` 说这件事要改 Result 和案件记录，这里不改：程序日志够排查，评估要用时再放进 `ModelUse`。

### D10 OpenAI 接口的工具调用带空的思考字段

实测（2026-11-06，主机，真实 key）：`deepseek-flash` 在历史里有工具调用时回答 400，错误是 `reasoning_content` 必须交回。历史只有文字时不报错。所以 `update-model-tool-loop` 的结论"三个模型都接受不带思考的历史"对 DeepSeek 不成立。

`OpenAIChatModel` 的 profile 设上 PydanticAI 给 DeepSeek 用的三项：字段名 `reasoning_content`、`field` 方式交回、工具调用必须带这个字段。字段内容是空的，DeepSeek 接受，历史仍然不带思考内容，所以切到别家的备用模型照样能用。

**不存下真实的思考内容：** 要进 `PmTurn`，切换模型时还要丢掉。**不关 DeepSeek 的思考：** 关思考是模型文件的设置，会让模型变差。

这三项对 `openai` 接口的所有厂商都生效。一次运行内的工具循环照样交回真实的思考内容，这是 PydanticAI 自己的行为。

### D11 失败的用量不算之前的对话

`capture_run_messages` 捕获的消息里包含之前的对话，所以带历史的运行第一个请求就失败时，用量算出来是 0/0，会记一条 0 费用。求和和取原文前先跳过之前的对话。

## 省略的情况

- 上下文的提示缓存：不做，先看真实的费用
- PydanticAI 在 Anthropic 接口的系统指令前面加一段说明 `<system>` 标签的话：框架的行为，不影响宪章和角色 prompt，不处理
- `effort` 和思考的设置：用模型的默认值，属于「切换真实模型」
- 一个回答同时调用两个交回工具：取第一个，已有规则
- 第 2 版快照的第一条消息和早期轮次里 s1 的条目 ID 并存：不处理，引用不校验存在性
- 不合法的原文很长：照样放进对话，不截断
- Critic 不看 PM 不合法的轮次：Critic 只看结论和 Result

## Risks / Trade-offs

- [回应时历史里有 `dispatch_tasks` 的调用，这次请求却没有这个工具] → 主机冒烟里 Sonnet 5.5、Opus 5.5 和 deepseek-flash 都接受
- [Sonnet、Opus 的思考关不掉，16,000 的输出上限可能截断工具调用] → 占位 prompt 下输出最多约 1,100 token；截断算输出不合法，走退回重交
- [别的 OpenAI 兼容厂商可能不认空的 `reasoning_content`] → 冒烟只测了 DeepSeek；出错时是调用错误，程序日志和告警能看到
- [不合法的原因是 pydantic 的原文，联合类型时很长] → 模型能读，日志也完整，真实比例出来前不改写
- [14 KB 的结论格式不合法的比例未知] → 程序日志写了每次的原因，上线后看比例
- [占位 prompt 下模型的回答没有意义] → 这个变更只验证通路，线上保持替身

## Migration Plan

没有迁移。部署以后模型文件还是替身，行为不变。回滚就是回到上一个提交。

## Open Questions

无
