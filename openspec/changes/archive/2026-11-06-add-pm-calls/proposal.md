# Proposal

## Why

`LlmBackend.pm` 和 `pm_respond` 还直接报错，PM 只能用替身。上下文、宪章、工具循环和模型文件都已经有了，缺的是把它们接成一次真实的 PM 调用，并规定 PM 的输出不合法时怎么办：适配器给出的输出不合法现在没有人接，案件工作流收到会直接出错。

## What Changes

- 新增 PM 调用：系统指令是宪章加角色 prompt，第一条消息是 PM 的上下文，之后是对话的每一轮
- 研究用 `dispatch_tasks`、`submit_conclusion` 交回，回应用 `respond_to_challenge` 交回
- 新增 PM 的角色 prompt 文件，内容只放占位，启动时读入
- PM 的输出不合法时退回重交：原文和原因作为一轮加进对话，和结论校验不通过共用重交次数，全案件共用；研究阶段用完是结论无效，回应阶段用完是系统故障；这次调用记费用，不切换模型
- 运行可以不带问题：对话里已经有上下文和工具结果时，模型直接接着回答
- 输出不合法带上原因，交给 PM 修改
- `openai` 接口历史里的工具调用带空的 `reasoning_content`：DeepSeek 开思考时缺它就拒绝请求
- 运行带之前的对话时，失败的用量不算之前的对话
- 唤醒 agent 的日志写模型自己回答的模型名，写输出不合法和它的原因
- 完整版 Proposal 写明 PM 输出不合法时的处理，「工具」表加 `respond_to_challenge`

## Capabilities

### New Capabilities

- `pm-calls`：调用 PM 时的系统指令和消息、对话怎样变成消息、交回工具、输出不合法变成什么、角色 prompt 文件

### Modified Capabilities

- `model-adapter`：运行可以不带问题，输出不合法带原因，回答带模型名，历史里的工具调用带空的思考字段，失败的用量不算历史
- `agent-calls`：PM 的输出和对话的轮次增加输出不合法
- `model-fallback`：PM 的输出不合法也算尝试成功，由调用方退回重交
- `case-stages`：输出不合法算退回重交，回应阶段可以退回，用完是系统故障
- `case-workflow`：PM 的研究和回应处理输出不合法
- `program-log`：唤醒 agent 写模型名和输出不合法

## Impact

- 代码：`contracts/agent_call.py`、`integrations/llm`、`integrations/standin`、`modules/gateway`（`ports.py`、`service.py`）、`modules/scheduler/domain/stage.py`、`workflows/case_workflow.py`、`workflows/case_flow.py`、`workflows/case_entries.py`、`bootstrap/app.py`
- 新文件：`src/tradingagentspro/bootstrap/pm_prompt.md`，进 git
- 数据：没有迁移，对话在工作流里，不进数据库的表
- 依赖和部署：不加依赖；不加配置项
- 行为：模型文件还是替身，上线这个变更线上行为不变；PM 配成真实模型时开始调用模型，但 prompt 是占位，要等「写 prompt」

## 引用的 Proposal 章节

- 完整版「PM」：看到的内容、工具、校验
- 完整版「Agent」：格式不合法视为无效，不算不可用，不切换模型；回应 Critic 质询的要求
- 完整版「共享上下文」：三层上下文按顺序注入最前面，宪章版本和快照版本写日志

## Non-goals

- 写宪章和角色 prompt 的正文：属于「写 prompt」
- 查询工具：属于「查询历史」，这个变更 PM 没有读取工具
- 开仓评分、观望规则：各有自己的变更
- Critic、子 agent、Reviewer 接真实模型，它们的输出不合法：在各自的变更里做
- 把模型名写进 Result 和案件记录：程序日志够查，要改存储的等 M6 评估真的需要
- 切换到真实模型：属于「切换真实模型」

## 需要人决定

无。完整版没有写 PM 的输出不合法以后怎么办，已经确认退回重交；研究阶段用完是结论无效，回应阶段用完是系统故障，apply 时写进完整版。
