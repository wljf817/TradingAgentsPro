# Proposal

## Why

PM 现在选继续关注不用付出什么：可以不说在等什么，入场条件满足了也可以不开仓、不解释，复查到期就续期，次数不限。这样模型总能以等下次财报、新闻或价格为由不动。真实模型接入以前，要让观望也有代价。

## What Changes

- 关注论点加一份承诺：在等什么、等到以后做什么；最晚等到哪天就是论点的复查日。新写的关注论点（升级、清仓转为关注、论点更新）没有承诺时退回 PM
- 承诺在关注论点里，案件上下文已经带着关注论点，所以入场案件自然带上承诺
- 入场条件满足开出的关注股事件和关注催化剂复查里，不开仓必须写明什么变了，否则退回 PM
- 股票池记录连续续期次数：转入关注时是 0，续期时加 1
- 到续期上限（默认 3，可配置）以后，复查类案件不能再选继续关注，只能开仓、降为候选或移出；案件上下文的允许的决定也去掉继续关注
- 开不了仓时（可用资金低于最小下单额，或者持仓名额为 0），续期不计入次数，到了上限也可以继续关注：原因是账户，不是拖延
- 替身跟着改：写承诺和什么变了，到上限时降为候选

引用完整版 Proposal 的章节：「Agent」的「PM」里的「观望」、「看到的内容」。

**Non-goals：**

- prompt 和宪章的内容：默认开仓、等的事发生由 PM 判断这部分靠 prompt，在「写 prompt」里写
- 新闻等代码认不出的"等的事发生"：代码只认入场条件满足和催化剂复查
- 观望的代价：复盘统计并展示给 PM，在 M6
- 降为候选再升回关注重新计数、PM 用备用模型到上限时只能降级或移出：都不加规则
- 持仓的续期：不限次数

## Capabilities

### New Capabilities

无

### Modified Capabilities

- `case-types`：关注的结论加"什么变了"；结论的校验加三项；新增续期上限和它的配置
- `stock-pool`：记录加连续续期次数；关注论点加承诺；状态变化和论点更新维护次数
- `case-workflow`：结论的检查传入是否到上限；结论生效记次数；允许的决定按上限去掉继续关注
- `agent-calls`：允许的决定跟着续期上限
- `stand-ins`：替身写承诺和什么变了，到上限时降为候选

## Impact

- 代码：`contracts/thesis.py`、`contracts/conclusion.py`、`modules/scheduler`（校验、案件定义、配置、允许的决定）、`modules/stockpool`（记录和论点更新）、`workflows/case_flow.py`、`workflows/case_conclusion.py`、`workflows/case_entries.py`、`integrations/standin/conclusions.py`
- 测试：`tests/contracts`、`tests/modules/scheduler`、`tests/modules/stockpool`、`tests/workflows`、`tests/integrations/standin`
- 数据：没有迁移。新增的项都可以缺，已存的论点、结论和股票池记录照常读回；旧的关注论点没有承诺，下次续期时写上
- 配置：`config/settings.toml` 多一个可选项，服务器不用改
- 部署：随下一次更新上服务器；替身到上限以后会把关注股降为候选
- 文档：完整版 Proposal 的「观望」写明开不了仓时不计入；两份路线表更新类型

## 需要人决定

无
