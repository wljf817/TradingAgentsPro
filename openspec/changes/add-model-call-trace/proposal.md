# Proposal

## Why

完整版 Proposal「程序日志」要求记下每次模型调用的完整输入和输出，但现在没有 spec 写这条，程序日志只有版本、任务和结果。子 agent 接上工具以后，它查了什么、工具返回了什么，运行完就没了：引用错误的失败查不出原因，M6 也没有比较 prompt 版本的原始材料。这件事要赶在新闻工具之前做，真实模型一上线就有记录。

## What Changes

- 每次向真实模型的尝试写一条调用记录：案件、任务、agent、模型、快照版本、起止时刻、用量、调用错误和这次尝试的全部消息
- 全部消息包括系统指令、之前的对话、模型的每个回答、每次工具调用和工具返回；拿到输出、输出不合法、调用失败都记
- 适配器给出这次尝试的全部消息，Agent 网关写入新表 `gateway_model_calls`，可以按案件读回
- 替身不调用模型，不写记录

**引用的完整版 Proposal 章节：**「程序日志」的 Agent 网关一行、「快照回放」所在的「记录、复盘与经验」。

**Non-goals：**

- 不给任何 agent 看调用记录，它不影响案件、下单、预算和备用模型
- 不做查看记录的命令或界面，M6 的复盘和回放要读时再接
- 不清理旧记录：程序日志不可删除
- 不改程序日志已有的条目

## Capabilities

### New Capabilities

- `model-call-trace`: 每次模型尝试的调用记录，写什么、什么时候写、怎么存、写入出错怎么办

### Modified Capabilities

- `model-adapter`: 一次尝试给出这次的全部消息，失败时也给出已有的部分
- `state-store`: 表的归属和迁移加上调用记录表 `gateway_model_calls`

## Impact

- `integrations/llm`：`client.py` 取消息，`backend.py`、`errors.py` 把消息带出来
- `modules/gateway`：`ports.py`、`service.py`、`repo.py`、`domain/`，`AgentCallService` 每次尝试写一条
- `bootstrap`：迁移 v0016，装配存储
- 主机：迁移在启动时自动运行，`deploy/` 不变；上线以后数据库每年大约多几个 GB
- 依赖不变

## 需要人决定

- 完整版「程序日志」把完整输入和输出写在程序日志里，但这些内容一条可以到几 MB，和一行一个动作的日志表放在一起不合适。**推荐：** 存在单独的表，仍算程序日志的一部分，同样只追加；完整版 Agent 网关一行加一句说明。这条不影响交易行为，我已按推荐写进 spec。
