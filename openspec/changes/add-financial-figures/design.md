# Design

## Context

`LlmBackend.sub_agent` 按子 agent 注册读取工具，Fundamentals 现在是 `filing_tools`。公司数据模块只有板块和财报：收盘心跳每天刷新股票池里每只股票的财报日历（往前 30 天、往后 90 天）和 surprise，按版本存，`VersionedStore.as_of` 已经能按时刻读。SEC 的请求都经过 `SecFilingSource`：一把锁、每个请求之后等 1/8 秒，代码表在进程里只下载一次。

2026-11-07 用 dev.env 的 SEC User-Agent 下载了 33 只股票的 companyfacts（含 ARM、TSM、SAP、NVO 和 BRK-B），并查了 Finnhub 的财报日历；下面的决定引用这次的数据。

## Goals / Non-Goals

**Goals:**

- 模型拿到的数字和比率都是代码从公司报告的数里取出、算好的，模型不用自己算
- 回放同一版快照时，季度数字和当时一样：只用当时已经提交的版本
- 银行、零售、52/53 周财年的公司都能列出季度

**Non-Goals:**

- 见 proposal 的 Non-goals

## Decisions

### D1：模块边界

涉及 `modules/companydata`、`integrations/sec`、`integrations/llm`、`bootstrap`。

| 位置 | 内容 |
| --- | --- |
| `modules/companydata/domain/financials.py` | `FinancialFact`；要取的科目和各项的顺序；季度的划分、取数、相减、截止、利润率、增长；`QuarterFigures`、`FinancialReport` |
| `modules/companydata/domain/earnings.py` | 超预期幅度、下一次发布 |
| `modules/companydata/ports.py` | `FinancialFactSource.financial_facts(symbol, concepts)`；`FinancialFactStore`（`add`、`facts(symbol, filed_before)`）；`EarningsStore` 加 `releases_as_of`、`surprises_as_of` |
| `modules/companydata/config.py` | `financial_quarters` |
| `modules/companydata/service.py` | `CompanyDataService` 加 `releases_as_of`、`surprises_as_of`；新增 `FinancialsService`：`fetch_facts`、`facts`、`report(symbol, before)`，财务事实的数据源和存储只交给它 |
| `modules/companydata/repo.py` | 财务事实表和 `PostgresFinancialFactStore`；财报的两个按时刻读 |
| `integrations/sec/edgar.py` | `SecFilingSource.financial_facts`：读 companyfacts，映射成 `FinancialFact` |
| `integrations/llm/financials.py` | `financials_tools(service)` 给出 `ToolBuilder`；结果的格式和说明的文字 |
| `integrations/llm/news.py`、`filings.py` | 说明加一句股票和时刻由程序给出 |
| `bootstrap` | 迁移 v0018；同一个 `SecFilingSource` 既给新闻和公告模块、也给公司数据模块；Fundamentals 的工具是公告工具加 `financials` |

模块之间传 `SubAgentRequest`、`EarningsRelease`、`SurpriseHistory`（已有）和 `FinancialFact`、`FinancialReport`（新增，`domain` 里的 frozen dataclass）。工具结果只在 `integrations/llm/financials.py` 里序列化给模型，不进 `contracts`。

### D2：数字取自 companyfacts，每次调用都取

companyfacts 一次给出一家公司全部 XBRL 事实和每一次提交的版本（AAPL 124 个时期里 25 个后来改过数），带提交日和 accession number，回放只要按提交日截止。另一个办法是解析 10-Q 的表格，排版五花八门，不做。Finnhub `/stock/metric` 只有今天的值，不能回放。

companyfacts 的大小 0.1 到 8.1 MB，下载 0.04 到 2.5 秒（JPM 最大）。和 `list_filings` 每次都问 submissions 一样（add-filing-tools 的 design D5），每次调用都取，不做"上次取完以后有没有新的定期报告"的判断：那要先查公告列表，多一个请求也多一条规则。只存 11 个科目的有起始日的事实，平均每只股票约 1,200 条，最多的 CRM 2,297 条；表的主键是股票、科目、单位、起始日、结束日、accession number，写入用 `ON CONFLICT DO NOTHING`。

### D3：截止用提交日早于时刻 T 的美东日期

companyfacts 只有提交日，没有受理时刻。EDGAR 美东 17:30 以后受理的文件提交日记为下一个工作日，所以提交日不早于受理日；提交日早于 T 的美东日期，就一定早于 T 受理。T 那一天提交的不用：要用就得再查 submissions 把 accession number 换成受理时刻。代价是 10-Q 提交当天 `financials` 还没有这一季，模型可以 `read_filing` 读原文；业绩当天的数本来就只在 8-K 的附件 99.1 里，XBRL 要等几周以后的 10-Q。

美东日期在 service 里换算，`zoneinfo` 不在纯区域的白名单里（同 add-news-tools 的 design D10）。

### D4：季度的划分和累计数相减

10-Q 的利润表有 3 个月的数，现金流量表只有年初至今的累计数；10-K 只有全年。所以季度的结束日取 3 个月或一年的事实的结束日，起始日取前一个结束日的下一天，金额不是 3 个月的就用同一起点的两个累计数相减。按结束日划分，不按 companyfacts 的 `fy`、`fp`：那是提交文件的财年和期间，上年同期的比较数也标着今年的 `fp`。

实测 29 家 us-gaap 季度数字齐全的公司（含 ARM 和 SHOP，不含换了 CIK 的 XOM），营收、净利润、经营现金流 8 个季度全部列得出来，其中第四季度和现金流的季度都靠相减。52/53 周的财年也对：KO 的季度结束日是 2026-04-03、2026-07-03、2026-10-02；COST 第四季度是 16 周（112 天），不在 80 到 100 天里，由全年减前 36 周得到。KO 2026 年第 2、第 3 季度的营收都是 13,380 百万美元，查过原始数据，确实相同。

EPS 和股数不相减：全年 EPS 减前三季度 EPS 不等于第四季度 EPS，股数也一样，Proposal 要求不估算，2026-11-07 已经确认。

### D5：每股数字和拆股

每一期用最近一次提交的版本，拆股以后的 10-Q 会把上年同期按新股数重报，但更早的季度不会重报。按 2024-11-01 截止，NVDA 的稀释 EPS 依次是 0.67、5.98、缺失、3.71、0.25、0.82……，拆股前后混在一起。同比增长用列表里往后第 4 个季度，而它最近一次的版本正是这个季度那份 10-Q 里的上年同期比较数，两个数口径相同：上例 0.67 比 0.25、5.98 比 0.82 都对。所以不做拆股换算，只在结果里写明口径，并列出稀释加权股数让模型看到跳变。换算要拆股的日期和比例，companyfacts 里没有可靠的来源，留给「估值」按价格的复权比算。

### D6：科目和顺序

| 项 | 实测 |
| --- | --- |
| 营收 | WMT、BRK-B 同时报 Revenues 和 RevenueFromContractWithCustomerExcludingAssessedTax，后者是不含会员费、保险等的一部分，所以 Revenues 在前。JPM、GS、WFC 的总营收只在 RevenuesNetOfInterestExpense，BAC 在 Revenues。COST 2026 年起不再报 Revenues，按季度取第一个有数的科目以后 8 个季度都列得出，重叠的季度两个科目的数相同。银行可能另报收费收入的 RevenueFromContract…，所以 RevenuesNetOfInterestExpense 排在它前面 |
| 资本开支 | AMZN、NVDA、HD 报 PaymentsToAcquireProductiveAssets，其余报 PaymentsToAcquirePropertyPlantAndEquipment；JPM、BAC、WFC、COIN 都没有 |
| 毛利、营业利润 | AMZN、COST、GOOGL、META、ORCL、V、UNH、WMT、NFLX 和银行没有毛利，银行和 NKE 没有营业利润：写缺失，不用营收减成本去算 |
| 稀释 EPS | BRK-B、V 没有（按股票类别报）：写缺失 |
| 单位 | EPS 在 COST、CRM、KO、WMT、PEP 下另有 `pure` 单位的几条，都是 2009、2010 年的错标，只用 USD/shares |

外国发行人：TSM、SAP、NVO 只有 `ifrs-full`，结果为空，写未覆盖；ARM 用 us-gaap 报 6-K 和 20-F，8 个季度都列得出。SPY 在代码表里但 companyfacts 返回 404，按没有财务事实处理，不报错（sec-filings spec 的例外）。

### D7：一致预期取状态库的版本

2026-11-07 查了 AAPL、JPM、NVDA、ARM 从 2024-06 到 2027-02 的 Finnhub 财报日历，免费 key 每只只给 2 行：最近一次和下一次。历史的预期和实际只能来自收盘心跳每天存下的版本和 Finnhub 的 surprise（最近 4 个季度的 EPS）。快照里已经有这些条目，但子 agent 的上下文没有，所以 `financials` 按时刻 T 读，口径和快照一样。

时刻 T 以前从没记下过的股票先刷新一次，2026-11-07 已经确认。刷新到的实际值可能晚于 T：快照到任务通常只隔几分钟，Finnhub 的实际值要几个小时才更新，影响很小；结果里写明是刷新时取的。不对刷新到的数再按发布日去掉实际值：surprise 没有发布日，要按财年季度去对，多一套规则。

### D8：工具和条目 ID

`financials` 没有参数：季度数、截止都由程序定，模型能改的只有调用不调用。条目 ID 是 `financials/<股票代码>`，不带 `s<版本>/`，和快照条目、`news/`、`filing/` 都不会相同；一次运行里结果只有一份，不细到季度。结果是 JSON：季度的数写原值，比率和增长写成 `"25.0%"` 这样的文字，相减得到的项列在每个季度的 `derived` 里，缺失的项写 `null` 并在说明里统一解释。

### D9：工具说明写明股票由程序给出

2026-11-07 的主机冒烟里 deepseek-flash 4 次有 2 次给 `list_filings` 多传了 `symbol`，参数校验失败，整次运行格式不合法。只改说明：每个读取工具的说明后面加一句股票和时刻由程序给出、参数里没有股票代码。不放宽参数校验：多余的参数被忽略，模型会以为查的是自己写的那只股票。

### D10：同一个 SEC 适配器

SEC 的频率上限按 User-Agent 和 IP 算，两个适配器各有一把锁就可能同时发请求。所以 `SecFilingSource` 同时满足新闻和公告模块的 `FilingSource` 和公司数据模块的 `FinancialFactSource`，bootstrap 只建一个。`financial_facts` 的失败按 sec-filings spec 分类，数据源不可用时抛公司数据模块的 `CompanyDataUnavailableError`；类名不改。

### 省略的情况

- XOM 2026-07 换了 CIK，旧 CIK 下的历史列不出来，只有 4 个季度
- 公司改了财年的结束月份：过渡期的季度可能划错
- 同一个时期在两份同一天提交的文件里（10-K 和 10-K/A 同一天）：按 accession number 取大的
- 非美元报告的 us-gaap 数字：不取，按缺失
- 刷新到的一致预期晚于时刻 T：只写明，不去掉
- 模型超时以后还在跑的工具线程继续写入：写入幂等，不取消
- 工具调用不写程序日志：内容已经在模型调用留痕里

## Risks / Trade-offs

- 每次 `financials` 都下载一次 companyfacts，大银行 8 MB → 主机冒烟时看 JPM 的耗时
- 工具次数默认 5：公告、文字稿、财务数字合起来常常超过 → 上线以后看实际次数再调 `max_sub_agent_tool_calls`
- Finnhub 的 EPS 实际值可能是调整后的口径，和 SEC 的稀释 EPS 不同，模型可能拿两个数直接比 → 结果里写明两个来源和口径
- 拆股前后的每股数字混在一起 → 写明口径，列出股数；同比增长不受影响（D5）
- companyfacts 的格式变了 → 原样报错

## Migration Plan

迁移 v0018 只新建一张空表，不用备份；随下一次更新部署，生产环境还是替身，替身不调用工具。回退时撤掉代码，表留着不影响别的模块。
