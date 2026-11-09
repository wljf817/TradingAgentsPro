# Proposal

## Why

Fundamentals 的「估值位置」任务要回答现在的估值在这只股票自身历史里处于什么位置，可现在它只有季度数字（`financials`），没有价格、PE、PS，也没有历史，只能让模型自己算、自己记。寻股候选在快照里没有行情，连现在的价格都看不到。这个变更给 Fundamentals 接上 `valuation` 工具，PE、PS、市值和它们过去几年的位置都由代码算好。

## What Changes

- 新增读取工具 `valuation`，没有参数：给出任务股票的价格、TTM EPS、TTM 营收、稀释加权股数，算好 PE、PS、市值，以及 PE、PS 在过去 5 年每个交易日里的分位、最低、中位、最高和用了多少天
- TTM 是上个财年全年加今年累计、减去年同期累计，EPS 和营收都这样算，公司不单独报第四季度 EPS 也能算
- 价格：快照里有这只股票的最新价就用它，否则用快照时刻那一天以前最后一个收盘价，结果写明用的是哪个
- 历史的每一天只用那一天以前提交的数字和那一天的原始收盘价；拆股前后的每股数字和股数按拆股比例换到那一天的口径
- 最近一期的结束日离那一天超过 200 天的数字不用，那一天没有 PE、PS
- TTM EPS 不大于 0 时写「亏损」，不给 PE，也不给 PE 的位置；PS 照给；历史上亏损的天数单独列出
- 每次调用都向 SEC 取这只股票的财务事实，和 `financials` 一样；没有季度财务数字的公司写明未覆盖
- Alpaca 日线适配器能取一只股票的原始收盘价，并能从 Alpaca 的公司行动接口取拆股记录；价格和拆股记录每次现取，不存
- 公司数据配置多两项：历史的年数（默认 5）和数字最旧多少天（默认 200）

引用完整版 Proposal 的章节：「Agent」的「Fundamentals」。

**Non-goals：**

- 和同行比估值：Proposal 不做
- EV/EBITDA、PB、股息率、自由现金流收益率等其他估值指标：Proposal 只要 PE、PS 和市值
- 用 Finnhub 的 PE、PS 或调整后的 EPS：只有今天的值，不能回放，口径也和 SEC 的稀释 EPS 不同
- ADR 和股票的换算比例：用 us-gaap 报季度数字的 ADR 按 1 比 1 算（design 的省略的情况）
- 财报当天新的一季算进 TTM：要等 10-Q 提交，和 `financials` 一样
- 把价格和拆股记录存进状态库：Alpaca 的历史收盘价和拆股记录不会变，回放时现取一样
- prompt 和宪章里怎样用这个工具：在「写 prompt」里写
- 切换真实模型：线上继续用替身

## Capabilities

### New Capabilities

- `valuation-tool`：`valuation` 的价格、TTM、PE、PS、市值、拆股换算、太旧和亏损的规则、历史位置、截止、补取、未覆盖、数据源不可用、引用和说明

### Modified Capabilities

- `alpaca-bars`：取一只股票的原始收盘价；取拆股记录；两者的失败分类
- `company-data`：配置多两项，历史的年数和数字最旧多少天
- `sub-agent-calls`：Fundamentals 的读取工具还有 `valuation`
- `bootstrap`：装配 `valuation`，价格和拆股记录用寻股粗筛的同一个 Alpaca 日线适配器

## Impact

- 代码：`modules/companydata`（domain 新增估值的规则、`ValuationService`、价格和拆股的端口、配置）、`integrations/alpaca/bars.py`、新增 `integrations/llm/valuation.py`、`bootstrap/app.py`
- 迁移：没有
- 测试：`tests/fakes/company_data.py`、`tests/modules/companydata`、`tests/integrations/alpaca`、`tests/integrations/llm`、`tests/bootstrap`
- 配置：`companydata` 一节多两个可选项，服务器不用改
- 部署：随下一次更新上服务器；生产环境还是替身，替身不调用工具
- 文档：完整版 Proposal「Fundamentals」的工具和工具规则写上 TTM 的算法、价格、历史的范围、太旧的数字和价格不存；两份路线表更新类型

## 需要人决定

无。2026-11-08 已经确认：TTM 用上个财年全年加今年累计、减去年同期累计（design D3），完整版「PE 用最近四个季度的 EPS」apply 时改成一致；历史看过去 5 年每个交易日，每天只用当时已经提交的数字（D5）；价格先用快照的最新价，没有时用快照那天以前最后一个收盘价（D6）；最近一期结束超过 200 天的数字不用（D7）；亏损时不给 PE 和 PE 的位置，PS 照给（D8）。
