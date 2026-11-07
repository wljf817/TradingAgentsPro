# Proposal

## Why

Fundamentals 现在只能读公告原文，拿不到按季度排好的营收、利润、EPS 和现金流，也看不到一致预期，「财报对比预期」「指引变化」只能让模型自己从原文里找数、自己算。这个变更给 Fundamentals 接上 `financials` 工具，数字由代码从 SEC 的财务事实取出、算好；顺带在全部读取工具的说明里写明股票由程序给出，因为 deepseek-flash 会多传一个 `symbol` 参数，整次运行变成格式不合法。

## What Changes

- 新增读取工具 `financials`，没有参数：按财季列出任务股票最近 8 个季度的营收、毛利、营业利润、净利润、稀释 EPS、稀释加权股数、经营现金流、资本开支，代码算好自由现金流、三个利润率和四项同比增长
- 季度的数字只用公司直接报告的 3 个月数字，或者同一起点的两个累计数相减（第四季度是全年减前三季度）；EPS 和股数只用直接报告的，第四季度一般缺失
- 只用美东日期早于快照时刻那一天提交的数字，每一期用当时最近一次提交的版本
- `financials` 还给出一致预期和实际值：状态库里快照时刻以前记下的财报发布和 EPS surprise，代码算好超预期幅度，标出下一次发布
- 快照时刻以前从没记下过这只股票的财报时（寻股的候选、刚加入股票池的股票），先向 Finnhub 刷新一次，结果里写明是快照时刻以后取的
- 每次调用都向 SEC 取这只股票的财务事实，存下没存过的；SEC 不可用时用存过的，并写明可能不全；SEC 没有这只股票、或者一个季度也列不出来时写明未覆盖
- SEC 适配器能读一家公司的财务事实（companyfacts）
- 公司数据模块存财务事实，能按时刻读财报日历和 surprise
- 新闻工具、公告工具和 `financials` 的说明都写明股票和时刻由程序给出，参数里不写股票代码

引用完整版 Proposal 的章节：「Agent」的「Fundamentals」。

**Non-goals：**

- `valuation`、PE、PS、市值：在「估值」变更
- 全年数字、资产负债表（现金、负债、股东权益）、分部数据：不列
- 第四季度的 EPS 用全年减前三季度推算：不做，Proposal 要求不估算
- 拆股前后的每股数字换算成同一口径：不做，同比增长不受影响（design D5）
- IFRS 和非美元的 us-gaap 数字：外国发行人写未覆盖
- 工具参数被多传时放宽校验：只改说明
- prompt 和宪章里怎样用这个工具：在「写 prompt」里写
- 切换真实模型：线上继续用替身

## Capabilities

### New Capabilities

- `financials-tool`：`financials` 的季度、各项数字、代码算的数、截止规则、一致预期、补抓、未覆盖、引用和说明

### Modified Capabilities

- `company-data`：财务事实的记录、取、读和存储；按时刻读财报日历和 surprise；配置多一项季度数
- `sec-filings`：读一家公司的财务事实
- `news-tools`：工具的说明写明股票和时刻由程序给出
- `filing-tools`：同上
- `sub-agent-calls`：Fundamentals 的读取工具还有 `financials`
- `bootstrap`：装配 `financials`；财务事实和公告共用一个 SEC 适配器
- `state-store`：多一张表 companydata_financial_facts

## Impact

- 代码：`modules/companydata`（domain 新增财务事实和季度的规则、`FinancialsService`、存储和表、配置）、`integrations/sec/edgar.py`、新增 `integrations/llm/financials.py`、`integrations/llm/news.py` 和 `filings.py` 的说明、`bootstrap/app.py`
- 迁移：v0018 新建财务事实表
- 测试：`tests/fakes/companydata.py`、`tests/modules/companydata`、`tests/integrations`、`tests/bootstrap`
- 配置：`companydata` 一节多一个可选项，服务器不用改
- 部署：随下一次更新上服务器，有一个迁移；生产环境还是替身，替身不调用工具
- 文档：完整版 Proposal「Fundamentals」的工具规则补上一致预期的来源和第四季度 EPS；两份路线表更新类型

## 需要人决定

无。已经确认：时刻 T 以前没有记下过财报的股票，调用时向 Finnhub 刷新一次，用刷新到的一致预期，结果里写明是时刻 T 以后取的（design D7）；第四季度的 EPS 和稀释加权股数按「不估算」写缺失，不用全年减前三季度推算（design D4）。完整版「Fundamentals」apply 时改成一致。
