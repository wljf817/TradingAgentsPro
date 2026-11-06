# Design

## Context

`LlmClient.ask` 发一个请求，用结构化输出（Anthropic 的 `NativeOutput`、OpenAI 接口的 `PromptedOutput`）拿结果。网关的 `ModelBackend` 约定一个方法就是一次尝试、一个请求。Proposal 要 News 和 Fundamentals 用工具循环，要 PM 用 `dispatch_tasks`、`submit_conclusion` 和查询工具。

实测（2026-11-06，真实 key，claude-sonnet-5-5、claude-opus-5-5、deepseek-flash）：

| 做法 | 结果 |
| --- | --- |
| 交回工具 + 文本，`tool_choice: auto`，用完整的 `DispatchTasks`、`SubmitConclusion` 格式 | 三个模型都能交回；不要 schema 的 strict |
| 同时有读取工具和交回工具 | Sonnet 在一个回答里同时调用两种，DeepSeek 分两个请求；两种都能走通 |
| 只回文字 | 拿到文本，要由代码当作不合法 |
| 代码重建的历史，不带思考内容 | 三个模型都接受，Opus 和 Sonnet 关不掉思考也不影响 |
| 次数用完后撤掉工具，或 `tool_choice: none` | Sonnet 和 Opus 回空内容；DeepSeek 把工具调用写成文字 |
| 次数用完后工具还在，只回提示 | 三个模型都用文字收尾 |

## Goals / Non-Goals

**Goals：** 一次尝试能跑完工具循环，结果用工具交回，两种接口走同一条路；后面的 PM 调用和子 agent 只写自己的内容。

**Non-Goals：** 接任何 agent，做任何工具，写 prompt。

## 模块边界

主要改 `integrations/llm`。`LlmClient.run` 的参数和返回都留在 integrations 内部，`LlmBackend` 用它实现网关的 `ModelBackend`。网关只改超时默认值和 `ModelBackend` 的说明，`ModelReply`、`ModelCallError` 不变，`usage` 变成一次尝试各请求之和。

## Decisions

### D1 一次尝试是一次完整的运行

超时、重试、切换备用模型和费用都按整次运行算；中途失败从头开始，查到的内容不保留。

**不让网关逐个请求驱动：** 那样要把循环状态存下来，换模型时还会把同一段工具调用混在两个模型里。Proposal 要求一个案件内不混用模型，从头开始正好符合，工具只读，重做没有副作用。

### D2 `run` 和 `ask` 分开

`ask` 不变，给没有工具的调用用，比如 Quant 的两步和 Reviewer。有工具或要二选一的调用用下面两个：

```python
def run[T](
    self,
    model: ModelRef,
    timeout: timedelta,
    instructions: str,
    question: str,
    replies: Sequence[ReplyTool[T]],
    tools: Sequence[ReadTool] = (),
    tool_limit: int = 0,
    history: Sequence[ModelMessage] = (),
) -> ModelReply[T]: ...


def run_text(
    self,
    model: ModelRef,
    timeout: timedelta,
    instructions: str,
    question: str,
    tools: Sequence[ReadTool],
    tool_limit: int,
    history: Sequence[ModelMessage] = (),
) -> ModelReply[str]: ...
```

`run_text` 没有交回工具，结果是文本，就是子 agent 现在交回 JSON 文本的方式。分成两个，调用方拿到的类型才准确。Quant 一次尝试调用两次 `ask`，用量由 `LlmBackend` 相加。

### D3 结果用交回工具给出

`ToolOutput` 列出交回工具，再加上 `str` 让 `tool_choice` 保持 `auto`。这样不会碰到 Opus 和 Sonnet 对强制 `tool_choice` 的 400，DeepSeek 思考时也接受，两种接口不用分别处理。`replies` 不为空时拿到文本就抛 `InvalidOutput`。

**不用结构化输出的联合类型：** 要处理 Anthropic 的 schema 复杂度限制，OpenAI 接口又只能靠 `PromptedOutput`，两家不一样。Proposal 本来就写的是工具。

### D4 次数用完后工具不撤

PydanticAI 的 capability `wrap_tool_execute` 计数，只算读取工具，超过 `tool_limit` 的调用不执行，回固定的一句提示。检查和计数之间没有 await，一个回答里的几次调用同时执行也不会算错。请求数上限是 `tool_limit + 2`：用满次数、一次看到提示、一次收尾。超过时按输出不合法处理。

### D5 校验失败一律不合法

`retries` 的 output 和工具都设 0。交回工具的参数不合法、调用不存在的工具、读取工具的参数不合法，都抛 `InvalidOutput`，由调用方按输出不合法处理，和现在的询问一致。读取工具自己抛的异常原样抛出，不归成调用错误。

### D6 对话由代码重建

`history` 是 `ModelMessage`，由 PM 调用从对话的轮次重建，不带思考内容。这样不依赖厂商的思考签名，切到别家的备用模型也能用。

### D7 用量求和

成功时用 `result.usage`。失败时从 `capture_run_messages` 里的各 `ModelResponse` 求和，一个回答都没有时是 `None`。

### D8 超时的默认值

工具循环最多 6 个请求（5 次工具加收尾），这次测的请求每个 1 秒左右，但提示很短；按读长公告、一次十几秒估，单次 150 秒。网关要求全部尝试放得进任务时限，2 × 150 × 2 = 10 分钟，任务时限跟着改。PM 和 Critic 也有工具循环，180 秒改成 300 秒。案件没有总时限，放宽不影响其他规则。

## 省略的情况

- Haiku 的阶梯价按各请求之和算，可能略有偏差：网关只有一个用量
- 流式请求：不用，超时管整次运行
- 读取工具自己抛出 `TimeoutError` 时会被当成超时：工具的数据源超时由工具自己说明，不抛出

## Risks / Trade-offs

- 超时的数字是估计：真实模型跑起来后看程序日志再调
- 模型可能在一个回答里同时派任务和提交结论：取第一个交回工具，其余忽略
- 两个变更都改 `task-dispatch`：先归档 `update-task-bar-range`
