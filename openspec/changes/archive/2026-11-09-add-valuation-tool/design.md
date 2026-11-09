# Design

## Context

Fundamentals 现在的读取工具是公告工具和 `financials`（`combined(filing_tools, financials_tools)`）。`FinancialsService` 每次调用都向 SEC 取 companyfacts、存下没存过的事实，按提交日早于时刻 T 的美东日期读出来；存的 11 个科目里已经有稀释 EPS、三个营收科目和稀释加权股数，估值不用多取科目。价格只在两个地方有：快照的行情条目（股票池里的股票才有），和寻股粗筛用的 `AlpacaBarSource`（按拆股调整的日线，`adjustment=split`）。

2026-11-08 用 dev.env 的 key 对 40 只股票算了一遍（含银行、亏损股、拆过股的 11 只、ARM、TSM、新上市的 CAVA），下面的决定引用这次的数据。

## Goals / Non-Goals

**Goals:**

- PE、PS、市值和历史位置都由代码算好，模型只读结果
- 回放同一版快照时结果和当时一样：每一天只用那一天以前提交的数字、那一天以前的拆股
- 拆股前后的数字放在同一个口径里比，拆股那天没有跳变

**Non-Goals:**

- 见 proposal 的 Non-goals

## Decisions

### D1：模块边界

涉及 `modules/companydata`、`integrations/alpaca`、`integrations/llm`、`bootstrap`。

| 位置 | 内容 |
| --- | --- |
| `modules/companydata/domain/valuation.py` | `DailyClose`、`Split`；TTM、拆股换算、PE、PS、市值、太旧和亏损、历史位置；`ValuationReport` |
| `modules/companydata/ports.py` | `PriceSource.daily_closes(symbol, start, end)`：原始收盘价；`SplitSource.splits(symbol, end)`：除权日不晚于 end 的拆股；失败都抛 `CompanyDataUnavailableError` |
| `modules/companydata/config.py` | `valuation_years`、`max_figures_age_days` |
| `modules/companydata/service.py` | 新增 `ValuationService(financials, prices, splits, config)`：`report(symbol, before, quote)`，取事实用 `FinancialsService.fetch_facts` 和 `facts` |
| `integrations/alpaca/bars.py` | `AlpacaBarSource` 加 `daily_closes`（`adjustment=raw`，一只股票）和 `splits`（公司行动接口），失败抛 `CompanyDataUnavailableError` |
| `integrations/llm/valuation.py` | `valuation_tools(service)` 给出 `ToolBuilder`：从请求的快照里找任务股票的行情；结果的格式和说明 |
| `bootstrap` | 一个 `AlpacaBarSource` 同时给寻股粗筛和 `ValuationService`；Fundamentals 的工具是公告工具、`financials` 加 `valuation` |

模块之间传 `SubAgentRequest`、`QuoteEntry`、`FinancialFact`（已有）和 `DailyClose`、`Split`、`ValuationReport`（新增，`domain` 里的 frozen dataclass）。工具结果只在 `integrations/llm/valuation.py` 里序列化给模型，不进 `contracts`。估值放在公司数据模块，不放寻股粗筛：它用的是财务事实，价格只是一个数据源；公司数据模块不能 import 寻股粗筛，所以 `DailyClose` 另外定义，不复用 `DailyBar`。

### D2：拆股用公司行动接口，价格用原始收盘价

一份文件里的每股数字和股数是提交那天的口径：拆股以后提交的 10-Q 会把比较期按新股数重报，之前提交的不会。所以一条事实换到某一天的口径，SHALL 用除权日晚于它的提交日、不晚于那一天的拆股：EPS 除以拆股比例，股数乘以比例。价格用那一天的原始收盘价，三者口径相同。NVDA 的 10-Q 2024-05-29 提交、EPS 5.98，除权日 2024-06-10（1 拆 10），之后的价格要和 0.598 比。

拆股记录来自 Alpaca 的 `/v1/corporate-actions`（`forward_split`、`reverse_split`），免费 key 能用，给出除权日和新旧比例，实测 11 只拆过股的股票全对得上；反向拆股是旧 8 新 1 这样（GE 2021-08-02）。另外两个办法：

- 用按拆股调整的日线：Alpaca 按"今天"调整，回放时价格里已经带着快照时刻以后的拆股；只给比值不会错，但只要结果里出现价格或每股数字，就泄露了以后的拆股
- 用原始价和调整价的比算拆股比例：要两次日线请求，比值有四舍五入的误差

实测原始收盘价除以以后的拆股比例，和调整价的差在 0.04% 以内；按上面的规则换算以后，NVDA、WMT、AVGO、CMG、AMZN、GOOGL、TSLA、LRCX、SMCI、NFLX、SHOP 在除权日前后一天的 PE 都没有跳变（NVDA 70.7 到 71.3，CMG 70.1 到 70.3）。

拆股请求的起点固定为 2000-01-01：Alpaca 最早的拆股记录在 2008 年前后（NVDA 2006、2007 年的拆股查不到），一只股票的全部拆股只有几条；终点是时刻 T 的美东日期，以后的拆股不取。

### D3：TTM 是全年加今年累计减去年同期累计

公司不单独报第四季度 EPS，四个季度直接相加做不到，2026-11-08 确认用：

- 全年：截止以内结束日最晚的、时长 350 到 380 天的事实
- 今年累计：起始日是全年结束日的下一天、时长 80 到 290 天的事实里结束日最晚的；没有时 TTM 就是全年
- 去年同期累计：起始日和全年相同、时长和今年累计相差不超过 10 天的事实；52/53 周的财年同一个季度会差 7 天

三项都按 financials-tool spec 的规则取最近一次提交的版本。营收按 financials 的科目顺序，用第一个算得出、而且不太旧的科目，三项来自同一个科目（COST 2026 年起不报 Revenues，见 add-financial-figures 的 design D6）。

和 Finnhub 的 `peTTM` 比，35 只里大多差 1% 到 3%；差得多的都查过原因：BAC 11.8 对 10.6、TSLA 394 对 440 是 Finnhub 用市值除以净利润；CAVA 107.5 对 46.5 是 Finnhub 的 `epsTTM` 1.68 错了，按申报的数是 0.54 + 0.51 − 0.50 = 0.55；C 17.0 对 10.2 是 companyfacts 里没有 C 2026 年的三份 10-Q。另外两个办法：四个季度 EPS 相加、第四季度用全年减前三季度，数一样但要的事实更多；TTM 净利润除以股数，不是 Proposal 说的 EPS。

### D4：市值和 PS

市值是价格乘以稀释加权股数，股数用结束日最晚的那条事实，结束日相同时用时长最短的（10-Q 同时报 3 个月和年初至今），按 D2 换到那一天的口径。PS 是市值除以 TTM 营收。

- 银行的营收按 financials 的科目顺序是扣掉利息支出的净营收，PS 比 Finnhub 高（JPM 4.4 对 2.9，BAC 3.0 对 2.3），和自己的历史比不受影响
- 两类股票的公司用这一类的价格乘以全部稀释股数；GOOGL 的稀释加权股数在 companyfacts 里只有大约 2 年，PS 的历史只有约 500 天

### D5：历史位置按每个交易日、当时的数字算

窗口是时刻 T 的美东日期往前 `valuation_years` 年（默认 5，同 Quant 日线范围的上限 1260 个交易日）到前一天。窗口里数据源给出原始收盘价的每一天都算一个点：用那一天的收盘价、那一天以前提交的事实、除权日不晚于那一天的拆股，和现在用同一套规则。实测 5 年里 TTM 在提交 10-Q、10-K 的那天跳一次（NVDA 2023-08-29 244 到 118），价格每天变，所以按天算，不按月取样；1,255 个点算起来很快。

位置给出分位（低于现在的天数占用了的天数的百分比）、最低、中位、最高和用了多少天，另列亏损的天数和没有数字的天数。实测 5 年里亏损的天不少：PLTR 428、DDOG 437、BA 1,061 天（共 1,255）。新上市的点少：ARM 613、CAVA 677 天。

### D6：价格先用快照的最新价

快照里有任务股票的行情、而且有最新价，就用它；否则用窗口里最后一个原始收盘价，也就是时刻 T 的美东日期以前最后一个交易日的收盘价。寻股在收盘以后跑，候选用的是前一个交易日的收盘价，晚一天；要用当天的收盘价，就得按交易日历和日线的延迟判断当天的日线齐了没有（stock-screen spec「截止交易日」），为一天的差别不值得。结果写明用的是哪个价格、什么时刻或哪一天的。

### D7：最近一期太旧的数字不用

一个点的 TTM EPS、TTM 营收或股数，最近一期的结束日离那一天超过 `max_figures_age_days` 天（默认 200）时，这一项那一天不用。按时申报的公司碰不到：最晚的情况是 10-K 提交前一天，最近一期是第三季度，离它约 91 天到财年结束、再加最长 90 天的 10-K 期限，约 181 天。实测只有 C（现在）、SMCI（2024–2025 年延迟申报，87 天）、SHOP（按 40-F 申报的年份，503 天）碰到。

### D8：亏损

TTM EPS 不大于 0 时 PE 写「亏损」，2026-11-08 确认不给 PE 的位置；历史上这样的天不算进 PE 的分位，单独计数。PS 照给。TTM 营收不大于 0 时没有 PS，按没有数字计。

### D9：请求和失败

每次调用依次：

1. 按 financials-tool spec「补取」向 SEC 取事实；SEC 没有这只股票、或者截止以内一个季度也列不出来时写明未覆盖，后面的请求都不发
2. 向 Alpaca 取窗口里的原始收盘价：一只股票 5 年约 1,260 根，一页
3. 向 Alpaca 取除权日不晚于时刻 T 的美东日期的拆股

SEC 不可用时用存过的事实，写明可能不全，和 `financials` 一样。Alpaca 的两个请求有一个不可用，就写明这次取不到，不给价格、PE、PS、市值和位置：没有拆股记录，拆过股的股票算出来会差几倍；只给快照的价格、不给别的，多一种结果的形状。

### D10：价格和拆股记录不存

Proposal 的工具规则是先查状态库、没有时抓取存下。这条是给会消失或者会改版本的数据定的：新闻、公告的文件、财务事实。历史收盘价和拆股记录过了那一天就不会变，回放时现取和当时一样，存下来只是多两张表。完整版 Proposal「Fundamentals」的工具规则 apply 时写明。

### D11：工具和条目 ID

`valuation` 没有参数，条目 ID 是 `valuation/<股票代码>`，结果里有价格时才算返回过的内容。结果是 JSON：价格带来源；TTM EPS、TTM 营收、股数各带最近一期的结束日；PE 是数、`"亏损"` 或 `null`；PS 和市值是数或 `null`；PE、PS 的位置各一组；窗口的起止日；说明。PE、PS、分位和最低、中位、最高保留一位小数，TTM EPS 保留两位，市值、TTM 营收、股数取整，都四舍五入；计算用不舍入的数。

`financials` 和 `valuation` 在一次运行里都调用时，companyfacts 下载两次（JPM 第一次 1.4 秒），不在两个工具之间缓存：缓存要按运行区分，多一个状态。

### 省略的情况

- 用 us-gaap 报季度数字的 ADR 按 1 份 ADR 等于 1 股算（ARM 正是 1 比 1）
- 快照没有最新价、窗口里也没有一个收盘价：原样报错；寻股候选都有一年以上的日线，股票池里的有行情
- 股票代码带点或横线（BRK.B 和 BRK-B）：SEC 和 Alpaca 写法不同，按代码原样请求
- 公司改了财年的结束月份：过渡期可能配不上去年同期，那几天没有数字
- XOM 换了 CIK，新 CIK 下没有全年的事实，算不出 TTM
- 分拆、特别分红：价格不调整，估值的变化照实算
- 拆股当天快照的最新价还是拆股前的成交：按除权日换算，当天的 PE 会差一个比例
- 模型超时以后还在跑的工具线程继续写入财务事实：写入幂等，不取消
- 工具调用不写程序日志：内容已经在模型调用留痕里

## Risks / Trade-offs

- Alpaca 漏了一次拆股 → 历史在那一天跳一个比例，现在的 PE 也会差这个比例；实测 11 只都没漏，冒烟时再看几只
- 财报当天新的一季还没进 TTM，价格已经动了 → 结果写明 TTM 截至哪一期、新一季要等 10-Q；「写 prompt」里说明
- GAAP 的一次性项目让 PE 大跳（AVGO 2024-09-12 从 68 到 142，是税务费用）→ 结果照实给，模型结合 `financials` 和公告判断
- 6 个工具共用每次运行 5 次的上限 → 上线以后看实际次数再调 `max_sub_agent_tool_calls`
- 每次调用一次 SEC 加两次 Alpaca 请求，约 1 到 3 秒 → 冒烟时看 JPM 的耗时

## Migration Plan

没有迁移，配置的两项都有默认值，服务器不用改；随下一次更新部署，生产环境还是替身，替身不调用工具。回退时撤掉代码即可。
