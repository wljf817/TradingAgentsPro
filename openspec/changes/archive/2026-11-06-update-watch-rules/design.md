# Design

## Context

关注论点已经有入场条件、催化剂和截止日期、复查日，案件上下文也已经带着它。续期也有定义：复查类案件里决定继续关注并附论点更新。开仓校验、退回重交、可用额度都在前面的变更里做好了。这个变更只补观望的三条规则。

论点和结论存成 JSON，读回时按 Pydantic 模型校验：股票池的版本表、案件记录的结论都是这样。服务器上这些表里已经有替身写的关注论点。

## Goals / Non-Goals

**Goals：** 继续关注有书面承诺；入场时不开仓要说明；连续续期有上限；账户开不了仓时上限不逼 PM 降级。

**Non-Goals：** prompt 内容；观望代价的统计；持仓的续期；识别新闻之类的"等的事发生"。

## 模块边界

| 模块 | 这次的事 | 传递的模型 |
| --- | --- | --- |
| contracts | `WatchCommitment`、`WatchThesis.commitment`、`WatchConclusion.what_changed` | 结论、论点 |
| scheduler | 结论的校验、`CaseDefinition.category`、`SchedulerConfig.max_watch_renewals`、`can_open`、`renewal_capped`、`allowed_decisions` 的新参数 | `Headroom`、`CaseDefinition`、`Conclusion` |
| stockpool | `PoolEntry.renewals`，`update_watch_thesis` 的新参数 `counted` | `WatchThesis` |
| workflows | 组案件上下文时算允许的决定；检查时算是否到上限；生效时算是否计入 | `CaseContext`、`Snapshot`、`PoolEntry` |
| integrations/standin | 写承诺和什么变了；到上限时降为候选 | `CaseContext`、`Conclusion` |

scheduler 不 import stockpool：是否到上限由 workflows 用 scheduler 的纯函数算好，作为 `renewal_capped: bool` 传给 `conclusion_rejection`。

## Decisions

### D1 承诺放进关注论点

`WatchThesis` 加 `commitment: WatchCommitment | None = None`，`WatchCommitment` 有 `waiting_for`（在等什么）和 `then_action`（等到以后做什么），两项都必填、不能是空白。

放进论点而不是结论，是因为论点已经进案件上下文、已经随续期整份替换，入场案件不用加任何东西就带上了承诺。放在结论里，就要再从案件记录找上一份承诺。

"最晚等到哪天"用复查日：它已经有 3 到 20 个交易日的范围，到期自动开出复查案件。另加一个日期，到期什么也不会发生。

### D2 旧数据不迁移，承诺可以缺

新字段做成可选，"必须写"放进结论的校验（`MISSING_COMMITMENT`），只查新写的论点。

做成必填就要改写股票池历史版本和案件记录结论里每一处 JSON，路径分散，还违反只追加、不覆盖。可选的代价是类型上多一个 None，只有读论点的人要面对：PM 的上下文写明没有，别处不读这一项。替身续期时补上承诺，旧论点过一个复查周期就有了。

`what_changed` 和 `renewals` 同样可缺，`renewals` 读回默认 0。

### D3 什么变了放在结论，由校验要求

`WatchConclusion` 加 `what_changed: str | None = None`。决定是开仓方案时写了是格式错误。

要不要写和案件有关，放进结论的校验（`MISSING_CHANGE`）：入场条件满足开出的关注股事件，或者关注催化剂复查，决定不是开仓方案时必须写。这是代码能认出"等的事到了"的两种案件。催化剂到期没兑现时，什么变了就写没兑现。

要认出入场条件满足，`CaseDefinition` 加 `category: EventCategory | None`，事件案件才有。

### D4 次数记在股票池

`PoolEntry.renewals: int = 0`。升级和清仓转为关注时设为 0，其他状态变化不动它，只有关注读它。

记在股票池而不从案件记录数：这是股票现在的状态，股票池已有一次写入；从案件记录数要重算"连续"和"开得了仓"，当时的快照也要再读。

结论生效时 `update_watch_thesis(..., counted=...)`：是续期并且开得了仓时为真。平时调整不计入。

### D5 开得了仓

`can_open(headroom)`：可用资金不低于最小下单额，并且持仓名额大于 0。熔断、降级模式、单只仓位上限都不算：只有这两项是账户本身放不下。

`renewal_capped(renewals, headroom, config)`：次数不小于上限，并且开得了仓。开不了仓时不算到上限，所以这时可以继续关注，次数也不加。否则次数停在上限，开不了仓的股票一样被逼降级。

可用额度取本案件的快照：组上下文时是第 1 版，检查时是最新的一版，PM 研究阶段是同一版。

### D6 允许的决定跟着上限

`allowed_decisions(case_type, renewal_capped=False)`：到上限时去掉继续关注。这个参数只在关注复查和关注催化剂复查时为真。上下文和校验用同一个判断，PM 看到的和代码查的一致。

### D7 校验的顺序

种类、跟进、承诺、续期缺论点更新、到上限、什么变了，然后是原来的子 agent、评分、研究要求。先查 PM 写的内容，再查研究。

### D8 替身

- 升级和关注的续期写固定的承诺；旧论点续期时也换上它，否则替身的续期会被退回
- 关注催化剂复查不开仓，继续关注和降为候选都写固定的"什么变了"
- 复查类案件的允许的决定没有继续关注时，提交降为候选

入场条件满足时替身开仓，不用写什么变了。

## 省略

- 旧的关注论点没有承诺时，事件案件不附论点更新地继续关注：不退回
- 开仓方案被 Critic 否决、风控拒绝或人工不确认：次数不变，复查日已到期的下次仍开复查案件
- 降为候选再升回关注：次数重新算
- PM 用备用模型并且到上限：不能开仓也不能继续关注，只能降级或移出
- 同一案件第 2 版快照的可用额度和第 1 版不同：允许的决定不重算

## Risks / Trade-offs

- 3 次续期最长可拖约 80 个交易日，模拟盘只跑几周，上限不一定起作用 → 上线后看观望和开仓的比例，再调次数
- 替身在服务器上每个复查周期续期一次，3 次后把关注股降为候选 → 替身的数据没有价值，正好验证这条路径

## Migration Plan

没有数据迁移。随下一次更新部署。回退时旧代码读新写的 JSON 会因为多余字段报错，所以回退要连带处理这些行；替身数据可以接受。
