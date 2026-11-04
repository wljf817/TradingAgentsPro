# Design

## Context

原因见 proposal.md。只涉及 `integrations/sec` 这一个适配器：`SecFilingSource` 把 submissions 映射成 contracts 的 `Filing`，公告模块和事件模块只读 `Filing.accepted_at`，不知道它是怎么来的，所以不用改。

受理时刻只在这个适配器里解析一次，同时用来和起始时刻比较、写进公告。

## Goals / Non-Goals

**Goals:**

- 新取到的公告受理时刻等于 SEC 公告页面上的时刻

**Non-Goals:**

- 见 proposal.md 的 Non-goals

## Decisions

### D1：直接读 acceptanceDateTime

acceptanceDateTime 以 Z 结尾，现在的值就是这个 UTC 时刻，适配器解析以后原样使用，不再用美东时区，也不用 `ZoneInfo`。

没有采用的做法：改读公告页面或 SGML 头里的时刻，这要为每份公告多发一个请求，而 submissions 已经给出了正确的值。

### D2：已经存下的公告不改

服务器上受影响的只有 2026-11-01 开始存下的公告和由它们产生的事件，时刻最多早 5 小时，都在替身阶段，不改。

- M6 回放的是 M5 以后真实模型的案件，那时这些公告已经过去很久，不会落在快照时刻前后 5 小时之内
- 快照回看公告按 65 个交易日算，差几个小时不影响结果
- 新闻和公告模块规定存下的内容不修改；这批数据不值得为它破例，或者写一次性的修复

没有采用的做法：写一个迁移按固定时差加回去。SEC 哪天改的不清楚，分不清哪些行是错的，而且公告已经由事件引用。

### D3：不做运行时检测

SEC 再改时，程序不会自动发现。真实接口的冒烟测试断言 2026-07-30 那份 8-K 的受理时刻，手动运行就能发现。这个情况很少见，不为它写运行时代码。

## 省略的情况

- SEC 再次改变 acceptanceDateTime 的含义：见 D3

## Risks / Trade-offs

- [SEC 再改回去] → 受理时刻又会偏，冒烟测试失败时才知道；影响和现在这个 bug 相同，不影响下单
- [切换当天，已经存过的公告不再写入] → 它们按 accession number 去重，不会重复开事件，时刻保持原样

## Migration Plan

归档并提交以后，按 `deploy/README.zh.md` 的「更新」步骤更新服务器，没有数据库迁移。回滚就是退回上一个提交。
