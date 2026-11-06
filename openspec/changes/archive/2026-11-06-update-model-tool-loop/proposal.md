# Proposal

## Why

现在一次模型尝试只能发一个请求，结果也只能用结构化输出交回。News、Fundamentals 和 PM 都要一边查工具一边回答，PM 还要在派任务和提交结论里二选一，而 Opus 和 Sonnet 不支持强制调用工具。这条路不通，PM 调用和子 agent 就都做不了，所以要先改。

## What Changes

- 一次尝试可以发多个请求：模型调用读取工具，代码执行后把结果交回，直到模型交出结果。重试和切换备用模型都从头开始
- `LlmClient` 加「运行」：带读取工具、交回工具、工具次数上限和之前的对话；「询问」不变
- 结果用交回工具给出，`tool_choice` 是 `auto`。模型只回文字算输出不合法
- 工具次数用完后工具仍在，再调用只回一句提示，模型据此收尾；请求数有上限，防止不收尾
- 之前的对话由代码重建，不带思考内容，这样换到别家的备用模型也能接着用
- 一次尝试的用量是各请求之和，失败时也给出已经拿到的部分
- 超时放宽：子 agent 单次 60 秒改 150 秒，PM、Critic、Reviewer 单次 180 秒改 300 秒，任务时限 5 分钟改 10 分钟

## Capabilities

### New Capabilities

无

### Modified Capabilities

- `model-adapter`：一次尝试的边界、用量、超时；新增运行、读取工具、交回工具、工具次数上限、之前的对话
- `agent-calls`：一次调用的说明；场景使用的超时写明
- `model-fallback`：超时的默认值，超时管整次尝试，重试从头开始
- `task-dispatch`：任务时限的默认值

## Impact

- 引用完整版 Proposal：「Task / Result 通信」的 PM 如何调用子 agent，「Agent」的备用模型，「News」「Fundamentals」的工具规则，「调度器与运行模块」的超时
- `integrations/llm/client.py`、`modules/gateway/config.py`、`modules/gateway/ports.py` 的说明
- 完整版 Proposal 的任务时限改成 10 分钟，News、Fundamentals 的上限规则跟着改
- 依赖 `update-task-bar-range`：两个变更都改 `task-dispatch`，这个后归档
- Non-goals：不接 PM、Critic、子 agent 的调用，`LlmBackend` 的五个方法还是没有实现；不写 prompt，工具用完的提示只是一句程序文字；工具本身在各自的变更里做

## 需要人决定

Proposal 写的是工具用完后「最后一次调用不给工具」，实测行不通：Sonnet 和 Opus 回空内容，DeepSeek 把工具调用写成文字。推荐按这个变更的做法改 Proposal，见 design D4。
