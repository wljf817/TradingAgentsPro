# Design

## Context

- `ModelBackend` 的五个方法每次向一个模型发一次请求，失败时抛 `ModelCallError`。重试、切换备用模型、记费用都在 `AgentCallService`。
- 案件工作流是同步的 DBOS 步骤，多个案件和子 agent 任务在不同线程里同时调用模型。PydanticAI 是异步的。
- 下面的结论都用 PydanticAI 2.66.0 对本地假服务器测过，还没有用真 key：
  - 8 个线程共用一个模型调用 `run_sync`，200 次里 80 次 `ModelConnectionError`；走一个后台事件循环，200 次全成功
  - SDK 默认把 429 重试到 3 个请求，`max_retries=0` 时 1 个
  - `retries={"output": 0}` 时不合法的输出只发 1 个请求，抛 `UnexpectedModelBehavior`，用量能从 `capture_run_messages` 取到
  - `claude-sonnet-5-5` 的结构化输出走 `output_config.format`；`OpenAIProvider` 加自定义地址走 `/chat/completions`，默认用 `tool_choice: required`
  - `PromptedOutput` 在 OpenAI 接口上把 JSON schema 写进系统消息，带 `response_format: json_object`，没有工具
- PydanticAI 的 DeepSeek 配置写明：Flash 和 V4 默认开 thinking，这时拒绝强制工具调用；Chat Completions 拒绝 `json_schema` 的 `response_format`。

## 模块边界

- 新的 `integrations/llm`：`LlmClient`（问一次）、`LlmBackend` 和 `VendorRouter`（实现 gateway 的 `ModelBackend`）、错误归类。它 import gateway 模块公开的 `ModelBackend`、`ModelReply`、`ModelCallError`、`CallError` 和 contracts 的 `ModelRef`、`TokenUsage`。
- `bootstrap/models.py` 生成模型文件、多读 `vendors`，`bootstrap/app.py` 建 `LlmClient` 和 `VendorRouter`。
- gateway 模块和 workflows 不改，端口不变。

## Goals / Non-Goals

**Goals:**

- 后面的变更只写 prompt 和转换，不再碰发请求、超时、错误和用量
- 线上行为不变：全部模型还是替身

**Non-Goals:**

- 校验模型名真实存在：只有发请求时才知道
- 给 OpenAI 兼容接口按厂商调参：地址和 key 以外没有按厂商的配置

## Decisions

### D1 依赖用 `pydantic-ai-slim[anthropic,openai]`

Proposal 定了 PydanticAI。完整的 `pydantic-ai` 还带 logfire、mcp、evals、cli，都用不到。已确认能和项目现有依赖一起解析。

### D2 按接口写代码，厂商写在模型文件

`vendors` 里每家写 `interface`、`base_url`、`api_key`。代码只有 `anthropic` 和 `openai` 两条路，加厂商不改代码，也可以换成中转地址。

- 用 PydanticAI 的 `deepseek:`、`zai:` 前缀：每家一个分支，加厂商要改代码
- 用 OpenRouter 一个入口：多一层依赖，模型文件的厂商校验也还要各写各的

替身也写进 `vendors`，接口 `standin`，分派只看这一个字段，不用判断厂商名字。

### D3 全部调用走一个后台事件循环

`LlmClient` 启动一个后台线程跑事件循环，询问的线程用 `asyncio.run_coroutine_threadsafe(...).result()` 等结果。每家厂商一个 SDK 客户端，只在这个循环里用。

- 每个线程 `run_sync`：测出 40% 的连接错误
- 每次新建客户端：能避开，但每次都重新握手，关闭也要逐个管

### D4 一次尝试只发一个请求

SDK 客户端自己建，`max_retries=0`，用 `anthropic_client`、`openai_client` 传给 provider。`Agent` 用 `retries={"output": 0}`。工具不注册，所以也不会有第二轮。

### D5 结构化输出按接口选

- `anthropic`：`NativeOutput`，请求带 JSON schema，模型的回答受 schema 约束，也不用被 Opus 5.5、Sonnet 5.5 拒绝的强制工具调用
- `openai`：`PromptedOutput`。工具输出要强制调用，DeepSeek 开着 thinking 时会拒绝；`json_schema` 的 `response_format` 也被拒绝。把 schema 写进系统消息、只要 JSON 对象，是 OpenAI 兼容接口里要求最低的做法

OpenAI 接口的输出没有 schema 约束，可能不合法，由调用方按「输出不合法」处理。文本输出（子 agent）两种接口都不受影响。

### D6 超时用 `asyncio.wait_for`

SDK 的 timeout 管的是单次读写，流式时只管两个分块之间。`wait_for` 管整次询问，到点取消请求，符合 model-fallback spec「尝试的超时」。

### D7 错误按 PydanticAI 的异常类归类

一个映射表，放在 `integrations/llm/errors.py`：

| 异常 | 调用错误 |
| --- | --- |
| `TimeoutError`、`ModelTimeoutError` | 超时 |
| `ModelConnectionError` | 连接失败 |
| `ModelRateLimitError` | 限流 |
| `ModelQuotaExceededError` | 额度或余额用完 |
| `ModelAuthenticationError` | 认证失败 |
| `ModelServerError`、`ModelOverloadedError`、`ModelUnavailableError` | 服务端出错 |
| 其他 `ModelAPIError`、`ContentFilterError` | 其他调用错误 |

模型拒绝回答走其他调用错误，网关切到下一个模型。这里不开 Anthropic 的服务端 `fallbacks`：服务端换模型以后，记下的模型和单价就对不上了。

### D8 不合法的输出用单独的异常

`LlmClient.ask` 抛 `InvalidOutput(text, usage)`，它不是 `ModelCallError`，不会触发切换模型。怎样交给案件由「PM 调用」等变更决定。子 agent 用文本输出，由网关校验，不走这里。

### D9 `VendorRouter` 只看厂商

`VendorRouter` 收 `厂商 → ModelBackend` 的映射，五个方法都按 `model.vendor` 转发。`LlmBackend` 的五个方法在这个变更里抛 `NotImplementedError`，各自的变更接上。

### D10 key 和地址、模型写在同一份文件

`RealVendor.api_key` 是 `SecretStr`。换厂商时只改一份文件，比地址在文件、key 在环境变量好改。代价是服务用户读得到 key，env 文件只有 root 能读；主程序本来就要拿到 key，这一点差别可以接受。

Pydantic 的校验错误默认带输入的内容，会把 key 打进启动失败的日志，所以读文件的模型设 `hide_input_in_errors=True`。

### D11 模型文件在 `config/`，启动时生成

路径是相对工作目录的 `config/models.toml`：systemd 的工作目录是代码目录，本地在仓库根目录。`config/` 写进 `.gitignore`，拉取代码不冲突，每个部署各有一份。

模板 `bootstrap/models.template.toml` 进 git、随包发布，全是替身。启动的装配这一步先检查文件，不存在就建目录、用 0600 建文件、写入模板。这样新部署不用手工准备就能启动，行为和以前一样。

- 指定路径的环境变量：两条路径都要维护，`config/` 已经能做到每个部署各一份
- 放 `/opt/tradingagentspro/` 或 `/etc`：离代码远，本地和主机的位置也不一样

全程序测试用 `BootstrapConfig(models_file=模板)`，不碰本机的 `config/`：里面可能有真模型和 key。工作流和替身的测试用 `tests/fakes` 的替身模型。

### D12 关掉横幅

设 `pydantic_ai.BANNER_ENABLED = False`，放在 `integrations/llm` 里，不改 systemd 单元。

### 省略的情况

- 模型文件给 agent 写了真模型，但对应的方法还没接上：抛 `NotImplementedError`，那个案件失败。在各 agent 的变更完成前不这样配
- thinking、effort、提示缓存：用模型的默认值
- 失败的请求没有用量：`ModelCallError` 的 usage 为 None
- Anthropic 接口的厂商不支持 JSON schema 输出：这种厂商改用 `openai` 接口
- 流式请求：输出上限 16,000 时不需要
- 模板以后加了必填项，已经生成的文件不会跟着变：启动失败，错误信息写明缺什么，手工补上
- 工作目录不对时会在别的地方生成文件：主程序只由 systemd 和仓库根目录启动
- 模型文件的权限不对：只在生成时设，不检查已有的文件

## Risks / Trade-offs

- [DeepSeek 等厂商的真实行为和假服务器不一样] → 任务 5.2 用真 key 在主机上每种接口各试一次，文本和结构化输出都试
- [OpenAI 接口的结构化输出靠提示词，失败率未知] → 接入 Critic 时看实际比例，不够再按厂商处理
- [PydanticAI 更新快，异常类可能变] → 版本写在锁文件里，错误归类有测试
- [输出上限截断回答] → 16,000 够结论和 thinking；截断时是输出不合法，能在日志里看到
- [模型文件不进 git，也不在备份里，没有历史，丢了要重写 key] → 地址和模型简单，key 能在厂商后台重新生成；部署说明写明这一点
- [有人把 key 写进模板] → 模板里只有替身和注释掉的示例；`config/` 被 git 忽略

## Migration Plan

没有迁移。线上继续是替身，部署时只多同步依赖，第一次启动时生成 `config/models.toml`。回退就是回到上一个提交，留下的 `config/` 没有影响。
