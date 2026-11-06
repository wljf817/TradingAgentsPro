# Design

## Context

原因见 proposal.md。风控的公式在 `modules/risk/domain/exposure.py` 和 `rules.py`，预留存在 `ReservationStore`，只有 `RiskService` 能读。快照由 `workflows/snapshot_flow.py` 组成，读执行模块和行情，整份存成 JSONB；账户里的持仓没有最新价，风控用的账户数据由 `workflows/account_snapshot.py` 把两者拼起来。案件上下文只在案件开始时组一次，随 DBOS 的 step 存下，不进快照和案件记录。

模块边界：`contracts.risk` 加 `Headroom`；`modules/risk` 算额度，`RiskService.headroom` 读有效预留；`contracts.snapshot` 的 `Snapshot` 带上 `Headroom`；`workflows/snapshot_flow` 调用风控服务；`bootstrap` 把风控服务传给 `SnapshotFlow`。

## Goals / Non-Goals

**Goals:**

- PM 拿到的数字和风控检查时用的算法相同
- 回放时能读到当时的额度

**Non-Goals:**

- 见 proposal.md 的 Non-goals

## Decisions

### D1：额度存进快照

Critic 要求补充研究时快照有新版本，额度跟着重算；案件上下文只算一次，会留下旧数字。回放也需要它：快照里没有预留，配置以后还可能改，事后算不回来。

没有采用的做法：放进 `CaseContext`。这是路线图原来的写法，改动更小，但有上面两个问题。Proposal 的共享上下文把额度列在案件上下文一层，这是 prompt 的分层，由共享上下文的变更决定它写在 prompt 的哪里，和存在哪里不冲突。

### D2：额度不是条目

`Snapshot` 加一个带默认值 None 的字段 `headroom`，不进 `entries()`，也不加 `EntryKind`。额度是算出来的参考数字，不是可以引用的事实。这样现有的条目、条目数、程序日志的说明和替身的引用都不变；共享上下文的变更按条目渲染事实时，子 agent 自然看不到它。

没有采用的做法：新增 `s<版本>/headroom` 条目。state-snapshot 和 program-log 的多个场景要改条目数，还要在共享上下文里再把它过滤掉。

### D3：公式放在风控模块

`modules/risk/domain/headroom.py` 是纯函数，输入股票、板块、账户数据、开仓预留和 `RiskConfig`，复用 `holding_of`、`holding_risk`、`sector_value` 和 `opening_reservation`。规则是"股数能否通过"，额度是同一个不等式解出来的上限，公式只能写两遍；spec「额度和风控一致」的场景用真的 `evaluate_plan` 测阈值两侧，防止两边走样。

`RiskService.headroom(symbol, sector, account)` 取全部有效的开仓预留，不拿锁：额度只是参考，方案最后由风控检查。

### D4：单只和风险各给一个合并后的数

单只仓位上限和单笔风险上限都取两条规则中较紧的，PM 不用知道背后是哪条规则。风险上限按这只股票合并后的计划风险给出，而不是"还能增加多少"：加仓时整只股票换用新的止损价，已有持仓的风险会变，只有合并后的上限对新建仓和加仓都成立。

### D5：只给美元

股数取决于 PM 写的限价和止损价，代码没法预先算。PM 算错了也安全，风控只往小调。

### D6：账户数据复用风控的拼法

`SnapshotFlow.take` 已经读了账户和全部持仓的行情，直接用 `account_snapshot(account, quotes)` 拼出风控用的账户数据，不再读一次券商，额度和这一版的账户、行情就是同一次读到的。案件股票不在持仓里时不需要它的价格。

### D7：哪些案件带额度

`snapshot_flow.py` 列出持仓和关注的 9 种案件类型。候选和寻股不能提交方案，复盘不唤醒 PM。

### D8：舍入

Decimal 除法算出的位数很长，给模型看没有意义。可用资金、单只仓位上限、单笔风险上限向下舍入到分，最小下单额向上舍入到分，这样按额度写的方案不会因为舍入而不通过。

### 省略的情况

- 这只股票已有开仓预留：风控按「未完成的开仓方案」拒绝，额度不特殊处理
- 额度算出负数：记 0，不写原因
- 持仓名额和最小下单额的矛盾（资金撑不起名额）：不处理，PM 看得到两个数
- 取不到行情的持仓按均价时不另写 WARNING：快照的行情条目已经缺它

## Risks / Trade-offs

- [PM 看到的额度过时：案件进行中别的方案占了预留，或者价格变了] → 风控按当时的数据调小或拒绝，确认消息里标明
- [两个并行的案件看到同一份额度] → 同上，后到的方案被调小

## Migration Plan

没有迁移。快照是 JSONB，新字段有默认值，旧快照读回时为空。部署后不用改配置。
