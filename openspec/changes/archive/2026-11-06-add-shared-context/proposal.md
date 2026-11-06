# Proposal

## Why

真实 PM 调用要读的事实分散在快照和案件上下文里：快照带着新闻全文，NVDA 一只股票 5 个交易日的摘要和正文约 75 万字符，标题只有约 1.2 万字符；系统宪章还不存在；案件上下文也没有允许的决定和研究要求。这个变更把这些内容组成 PM 调用要用的上下文，并记下每次调用用的宪章版本和快照版本。

## What Changes

- 新增系统宪章：宪章文件写版本和文字，文字暂时是占位内容；启动时加上由配置生成的风控限额
- 新增 PM 的上下文：由快照和案件上下文组出共享事实和案件内容，事实都带条目 ID
- 新闻和公告只给标题：新闻是标题、来源和发布时刻，公告是表单类型、item 和受理时刻；不限条数
- 事件和已确认的结论只有案件股票的；其他股票只有股票池的代码、状态和板块，以及持仓和行情
- 案件上下文补上允许的决定和研究要求的子 agent 数
- 唤醒 agent 的程序日志写上宪章版本和快照版本
- 完整版 Proposal「共享上下文」写明事件和已确认的结论的范围

## Capabilities

### New Capabilities

- `shared-context`：系统宪章，以及 PM 的上下文的内容、标题的写法和事实的范围

### Modified Capabilities

- `agent-calls`：案件上下文增加允许的决定和研究要求
- `case-workflow`：组出案件上下文时给出这两项
- `program-log`：唤醒 agent 的说明增加宪章版本和快照版本

## Impact

- 代码：`contracts`、`modules/gateway/domain`、`modules/scheduler/domain/case_type.py`、`workflows/case_flow.py`、`workflows/case_entries.py`、`bootstrap`
- 新文件：`src/tradingagentspro/bootstrap/charter.toml`，进 git
- 数据：没有迁移，案件上下文不进数据库的表
- 依赖和部署：不加依赖；不加配置项
- 行为：替身不读上下文和宪章，上线这个变更只让唤醒 agent 的日志多两项

## 引用的 Proposal 章节

- 完整版「共享上下文」：三层内容、Shared State 规则、宪章版本和快照版本写入程序日志
- 完整版「PM」：PM 看到的内容
- 完整版「Agent」：系统宪章放评分定义和输出格式，风控限额由配置生成

## Non-goals

- 写宪章和角色 prompt 的正文：属于上线组的「写 prompt」
- 调用模型、把对话轮次转成消息、接上 `LlmBackend`：属于「PM 调用」
- Critic 和子 agent 的上下文，子 agent 的 Result 日志写宪章版本：在它们各自的变更里做
- 经验和历史案件的数量索引：属于「查询历史」
- 观望的承诺字段：属于「观望规则」

## 需要人决定

无。完整版「PM」看到的内容没有列财报日历和 surprise，已经确认给 PM，apply 时补进完整版「PM」。
