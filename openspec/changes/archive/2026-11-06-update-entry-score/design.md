# Design

## Context

调度器的 `conclusion_rejection` 是纯函数，按 case-types spec「结论的校验」的顺序返回第一项不通过的原因。案件工作流的 `conclusion_problem` 调用它，`workflows/case_entries.py` 把原因换成退回给 PM 的文字。动机见 proposal.md。

本次只涉及调度器模块和案件工作流；模块之间传递的还是 `Conclusion` 和 `ConclusionRejection`，没有新模型。

## Goals / Non-Goals

**Goals:**

- 决定是开仓方案或加仓时，评分不大于 0 的结论不生效

**Non-Goals:**

- 判断评分写得对不对：这是 M6 评估的事

## Decisions

### D1：检查放在调度器的结论校验里

`Decision.BUY` 和 `Decision.ADD` 只出现在关注的结论和持仓的结论里，两种结论都有 `signal`。这一项按决定检查，和研究要求一样，所以放在 `conclusion_rejection`。放进 Agent 网关的 `conclusion_signal_rejection` 也能读到信号，但那里只查信号本身的格式，不看决定。

### D2：排在研究要求之前

一份结论两项都不通过时，先说评分。PM 先知道决定和评分矛盾，就不会为了补研究先用掉一次重交。

### D3：0 不做成配置

0 是评分刻度里的中性，大于 0 才是看多，这条边界来自刻度的定义，不是可以调的阈值。Proposal 也没有要求配置。

### 省略的情况

- 回应 Critic 时修改方案：回应不带信号，评分还是结论里的，不再检查
- 替身的 `signal_score` 配成不大于 0：开仓和加仓都会被退回，这是配置的结果，不特殊处理

## Risks / Trade-offs

- [模型可以随手写 +1 通过] → 这一项只拦决定和评分的矛盾；评分准不准由 M6 按评估窗口统计
- [评分比的是相对板块的超额收益，板块下跌时也能大于 0] → 这是评分刻度的定义，不在本次改动
