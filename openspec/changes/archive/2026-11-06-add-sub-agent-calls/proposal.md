# Proposal

## Why

`LlmBackend.sub_agent` 还直接报错，News 和 Fundamentals 只能用替身。工具循环和模型文件都已经有了，缺的是把它们接成一次真实的子 agent 调用：请求现在带整份快照（NVDA 约 75 万字符），输出要求纯 JSON 文本（实测 Flash 和 Haiku 都会包上文字和代码块），网关也不查引用。后面的新闻、公告、财务和估值工具都要接在这次调用上。

## What Changes

- 新增子 agent 调用：系统指令是宪章加角色 prompt，唯一一条消息是子 agent 的上下文，只接上 News 和 Fundamentals，Quant 等它自己的变更
- 新增子 agent 的上下文：任务内容、快照时刻、市场状态、这只股票的最新价和前收盘价、相关事件、任务的输入条目；不含持仓、现金、股票池和已确认的结论
- 用 `submit_signal`、`submit_no_signal` 交回，子 agent 的输出从 JSON 文本改成校验过的信号、无信号或输出不合法 **BREAKING**（内部接口）
- 记下一次运行的工具返回过的内容的 ID，网关校验引用：引用的条目必须在这版快照里或者是这次运行的工具返回过的，否则任务失败，原因是格式不合法，无信号也查
- 读取工具按子 agent 注册，这个变更里都是空的；工具次数上限默认 5 次，按每次运行算
- 新增 News、Fundamentals 的角色 prompt 文件，内容只放占位，启动时读入
- 运行子 agent 的任务写一条唤醒 agent，带宪章版本和快照版本
- 完整版 Proposal：子 agent 的输入改成窄版，工具次数改成每次运行

## Capabilities

### New Capabilities

- `sub-agent-calls`：调用子 agent 时的系统指令和消息、子 agent 的上下文、交回工具、工具返回过的内容、工具次数、输出不合法变成什么、角色 prompt 文件

### Modified Capabilities

- `agent-calls`：子 agent 的输出和它看到的内容，子 agent 的任务按输出不合法和引用生成 Result
- `task-dispatch`：子 agent 的输出变成 Result，加上引用的校验
- `signal-format`：新增子 agent 引用的条目的校验
- `stand-ins`：子 agent 替身交回信号，不再是 JSON 文本
- `program-log`：运行子 agent 的任务写唤醒 agent
- `settings-file`：`gateway` 一节加工具次数

## Impact

- 代码：`contracts/agent_call.py`、`contracts/sub_agent_context.py`（新）、`modules/gateway`（`config.py`、`ports.py`、`service.py`、`domain/context.py`、`domain/result.py`）、`integrations/llm`（`backend.py`、`router.py`、`sub_agent.py`（新））、`integrations/standin/backend.py`、`workflows/case_flow.py`、`workflows/case_entries.py`、`bootstrap/app.py`、`bootstrap/charter.py`
- 新文件：`src/tradingagentspro/bootstrap/news_prompt.md`、`fundamentals_prompt.md`，进 git
- 数据：没有迁移，Result 的格式不变，引用的条目只是多了不带版本的 ID
- 依赖和部署：不加依赖；加一个配置项，有默认值
- 行为：模型文件还是替身，上线这个变更线上行为只多一种唤醒 agent 的日志；News、Fundamentals 配成真实模型时开始调用模型，但没有数据工具，prompt 也是占位，要等后面的变更

## 引用的 Proposal 章节

- 完整版「News」「Fundamentals」：输入、工具规则、输出和引用
- 完整版「Agent」：信号统一格式，格式不合法视为无效，不算不可用，不切换模型
- 完整版「Task / Result 通信」「共享上下文」：子 agent 看到的内容，宪章版本和快照版本写日志

## Non-goals

- 新闻、公告、财务和估值工具：各有自己的变更，这个变更只留接入的地方
- Quant 接真实模型：它是固定工作流，还没有拆分
- 写宪章和角色 prompt 的正文，包括评估窗口的选项：属于「写 prompt」
- 寻股候选的最新价：快照里没有行情时留空，由「估值」解决
- 引用写错时打回重交：先按 Proposal 判失败，上线以后看失败率再定
- 校验 PM 结论里的信号引用的条目
- 切换到真实模型：属于「切换真实模型」

## 需要人决定

无。已经确认：子 agent 用窄版输入，完整版「Task / Result 通信」「共享上下文」apply 时改成一致；引用写错按 Proposal 判失败；寻股最新价留空。

工具次数改成每次运行是实现上的决定：换模型重跑时，新模型看不到之前的工具结果，按任务算会让它没有工具可用，和 `query_history` 同样处理，完整版 apply 时跟着改。
