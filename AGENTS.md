# AGENTS.md

项目结构和常用命令见 [README.md](README.md)，规划和编码约定见 [openspec/config.yaml](openspec/config.yaml)。

## 两份 Proposal 的用法

- 简版（`docs/AI Agent 美股交易系统需求 Proposal 简版.md`）已经固定，不可更改，也不对它提修改建议
- 完整版（`docs/AI Agent 美股交易系统需求 Proposal.md`）是写 spec 的参考，只读当前变更对应的章节
- 当前变更对应的完整版章节有问题（矛盾、缺口、不可行）时，主动提出并给出推荐做法
- 完整版其他章节的问题，在人没有要求时一律忽略，不报告

## 审核 OpenSpec 变更时

审核范围是 `openspec/changes/<变更名>/` 和它改动的代码，以及这个变更对应的完整版 Proposal 章节：

- 变更内部是否自洽（proposal、specs、design、tasks 之间）
- 是否符合对应的 Proposal 章节，以及 `openspec/config.yaml` 的架构规则；对应章节本身有问题也指出
- spec 是否覆盖边界和异常情况，tasks 是否先写测试
