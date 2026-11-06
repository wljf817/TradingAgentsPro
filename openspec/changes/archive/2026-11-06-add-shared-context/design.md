# Design

## Context

原因见 proposal.md。`PmRequest` 已经带案件上下文、快照和对话，`LlmClient.run` 分开收 `instructions`、`question` 和 `history`。快照的 `news` 存整篇 `NewsArticle`，`filings` 存 `Filing`，`Snapshot.headroom` 不是条目。案件类型固定的结论种类在 `modules/scheduler/domain/case_type.py`，研究要求的子 agent 数在 `SchedulerConfig`，`CaseFlow.context` 组出 `CaseContext`。`CaseFlow._called` 写唤醒 agent 的日志。

纯区域不能用 pydantic 和 json，模块之间不能互相 import。

模块边界：

- `contracts`：新增 `Charter`（版本、文字）；新增 PM 上下文的模型 `PmContext`、`NewsHeadline`、`FilingHeadline`；`CaseContext` 加 `decisions` 和 `research_agents`
- `modules/gateway/domain`：纯函数 `pm_context(request: PmRequest) -> PmContext`，由网关包根导出
- `modules/scheduler/domain`：给出案件类型允许的决定
- `workflows`：`CaseFlow.context` 填新的两项；唤醒日志写版本
- `bootstrap`：读宪章文件，加上 `RiskConfig` 组成 `Charter`，把版本交给 `CaseFlow`

```
charter.toml + RiskConfig --> Charter
                                 |
PmRequest --> pm_context --> PmContext.model_dump_json()
                                 |
LlmBackend（PM 调用的变更）：instructions = 宪章 + 角色 prompt
                             question     = PM 的上下文
                             history      = 对话
```

## Goals / Non-Goals

**Goals:**

- PM 读到的事实只来自代码，都能引用
- 组上下文不依赖模型库，测试不用真实模型
- `pm_context` 组出的内容在 PM 调用的变更里直接用

**Non-Goals:**

- 见 proposal.md 的 Non-goals

## Decisions

### D1：上下文是纯函数，放在网关的 domain

Proposal 规定由 Agent 网关注入。`pm_context` 不做 I/O，输入相同输出相同，`LlmBackend` 经网关包根调用。替身继续直接读 `PmRequest`。

没有采用的做法：放进 `integrations/llm`。那样规则离开了网关，测试还要依赖模型层。

### D2：用 Pydantic 模型转成 JSON

`PmContext` 分 `shared` 和 `case` 两段，直接嵌套快照的条目和 `CaseContext`，再用 `model_dump_json()` 转成文字。条目 ID、时刻和 Decimal 都按原样，没有的项是 `null` 或空列表，不用写模板。

没有采用的做法：写 Markdown 表格。代码多，还要逐项处理缺失和数字格式。等真实 PM 跑起来，确实读不好 JSON 再改。

### D3：上下文放第一条消息，宪章放系统指令

宪章在进程里不变，放进 `instructions`；每个案件不同的内容放进 `question`，对话放进 `history`。这样 Proposal 的"注入 prompt 最前面"的顺序成立，也不用改 `LlmClient`。

### D4：宪章文件进 git，版本由人写

`src/tradingagentspro/bootstrap/charter.toml` 写 `version` 和 `text`，放在代码旁边，用 git 记录改动，符合"改动需人工确认"。`text` 是一句占位，正文在「写 prompt」里写。

没有采用的做法：放进 `config/`。那里不进 git，各机器可以不同，回放时也说不清用的是哪份。用内容哈希做版本也不采用：Proposal 要的是版本号，日志里读不出先后。

### D5：风控限额由 bootstrap 拼进文字

`bootstrap/charter.py` 读文件，把 `RiskConfig.model_dump_json()` 接在文字后面，组成 `Charter`。网关不用知道风控模块。全部配置项都放，不挑选，挑选的话新增阈值时还要记得改这里。

### D6：允许的决定和研究要求放进 `CaseContext`

两项都是案件类型决定的，`workflows` 能读 scheduler 和 `SchedulerConfig`，网关不能。`decisions` 是 `Decision` 的元组，寻股为空；`research_agents` 寻股是 None。研究要求的子 agent 数取 `self._configs.scheduler`。

### D7：唤醒日志的版本

`CaseFlow` 构造时收 `charter_version`，`_called` 把它和 `snapshot.version` 传给 `wake_text`、`wake_failed_text`，末尾加"宪章版本 n，快照 s<版本>"。PM、Critic、Reviewer 走同一条路，一起写上。替身没有读宪章，日志仍写当前版本：它表示这次调用对应的版本，上线前的日志不做评估用。

### 省略的情况

- 标题不限条数：标题很短，钱不是限制
- 不过滤表单 4：一条一行，PM 看到数量就够了
- 不校验版本是否递增：版本由人写，靠代码审查
- 风控配置改了版本不变：配置和默认值不同的项启动时已写日志
- 第 2 版快照之前引用的 s1 条目不再显示：这些引用已经写在对话里

## Risks / Trade-offs

- [新闻标题是外部文字，可能含有指令] → 留在 JSON 的字符串字段里；「写 prompt」时在宪章写明只当资料
- [JSON 比表格多占一些 token] → 钱不是限制，标题约几千 token；有问题再改 D2
- [部署时有在途的案件，已存下的 `CaseContext` 没有新的两项] → 替身不读这两项，案件照常跑完

## Migration Plan

没有数据迁移。按日常更新部署，新增的 `charter.toml` 随代码拉取，不用手动操作。回滚就退回上一个提交。
