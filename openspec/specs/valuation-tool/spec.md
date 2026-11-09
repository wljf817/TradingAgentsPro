# valuation-tool Specification

## Purpose
Fundamentals 的读取工具 `valuation`：给出任务股票的价格、TTM EPS 和营收、PE、PS、市值，以及 PE、PS 在过去几年每个交易日里的位置。每一天只用那一天以前提交的数字和那一天以前的拆股，数字缺的向 SEC 补取，价格和拆股记录向 Alpaca 现取。

## Requirements

### Requirement: 估值工具

向真实模型调用 Fundamentals 时，SHALL 给出读取工具 `valuation`（model-adapter spec「读取工具」），作用是给出任务股票的估值和它在这只股票自身历史里的位置。

- `valuation` MUST NOT 有参数：股票是任务的股票，时刻是这次运行读的这版快照的生成时刻，由程序给出
- 工具的说明 SHALL 写明股票和时刻由程序给出，参数里没有股票代码
- 工具 SHALL 只读：除了「补取」存下财务事实，不改变任何记录
- 工具次数按 sub-agent-calls spec「工具次数」算，和公告工具、`financials` 合在一起
- News、Quant、PM、Critic 和 Reviewer SHALL NOT 有这个工具；替身不查询

本 spec 的场景除非另有说明，都使用下面的基准数据：

- T-4 是 Fundamentals 的任务，股票 BBB，读 S1；S1 的生成时刻是 2026-10-14 15:00:00Z（美东 2026-10-14），时刻 T 指这个时刻；S1 有 BBB 的行情：最新价 23，前收盘价 22.5，更新时刻 2026-10-14 14:59:00Z
- `valuation_years` 是 1，`max_figures_age_days` 是默认的 200（company-data spec「公司数据配置」）；没有写明的其他配置都是默认配置
- SEC 上 BBB 的财务事实如下，金额单位是美元，EPS 是美元每股，股数单位是股；时长是结束日减起始日：

  | 事实 | 科目 | 起始日 | 结束日 | 数值 | 表单 | 提交日 |
  | --- | --- | --- | --- | --- | --- | --- |
  | E1 | EarningsPerShareDiluted | 2024-01-01 | 2024-12-31 | 1.60 | 10-K | 2025-02-20 |
  | E2 | EarningsPerShareDiluted | 2024-01-01 | 2024-06-30 | 0.80 | 10-Q | 2025-07-30 |
  | E3 | EarningsPerShareDiluted | 2025-01-01 | 2025-06-30 | 1.00 | 10-Q | 2025-07-30 |
  | E4 | EarningsPerShareDiluted | 2025-01-01 | 2025-12-31 | 2.00 | 10-K | 2026-02-20 |
  | E5 | EarningsPerShareDiluted | 2025-01-01 | 2025-03-31 | 0.24 | 10-Q | 2026-04-29 |
  | E6 | EarningsPerShareDiluted | 2026-01-01 | 2026-03-31 | 0.29 | 10-Q | 2026-04-29 |
  | E7 | EarningsPerShareDiluted | 2025-01-01 | 2025-06-30 | 0.50 | 10-Q | 2026-07-29 |
  | E8 | EarningsPerShareDiluted | 2026-01-01 | 2026-06-30 | 0.60 | 10-Q | 2026-07-29 |
  | E9 | EarningsPerShareDiluted | 2026-01-01 | 2026-09-30 | 0.95 | 10-Q | 2026-10-14 |
  | E10 | EarningsPerShareDiluted | 2025-01-01 | 2025-09-30 | 0.75 | 10-Q | 2026-10-14 |
  | R1 | Revenues | 2024-01-01 | 2024-12-31 | 3400 | 10-K | 2025-02-20 |
  | R2 | Revenues | 2024-01-01 | 2024-06-30 | 1600 | 10-Q | 2025-07-30 |
  | R3 | Revenues | 2025-01-01 | 2025-06-30 | 1900 | 10-Q | 2025-07-30 |
  | R4 | Revenues | 2025-01-01 | 2025-12-31 | 4000 | 10-K | 2026-02-20 |
  | R5 | Revenues | 2025-01-01 | 2025-03-31 | 950 | 10-Q | 2026-04-29 |
  | R6 | Revenues | 2026-01-01 | 2026-03-31 | 1150 | 10-Q | 2026-04-29 |
  | R7 | Revenues | 2026-01-01 | 2026-06-30 | 2400 | 10-Q | 2026-07-29 |
  | R8 | Revenues | 2025-01-01 | 2025-06-30 | 1900 | 10-Q | 2026-07-29 |
  | W1 | WeightedAverageNumberOfDilutedSharesOutstanding | 2025-04-01 | 2025-06-30 | 1050 | 10-Q | 2025-07-30 |
  | W2 | WeightedAverageNumberOfDilutedSharesOutstanding | 2025-01-01 | 2025-12-31 | 1040 | 10-K | 2026-02-20 |
  | W3 | WeightedAverageNumberOfDilutedSharesOutstanding | 2026-01-01 | 2026-03-31 | 2040 | 10-Q | 2026-04-29 |
  | W4 | WeightedAverageNumberOfDilutedSharesOutstanding | 2026-04-01 | 2026-06-30 | 2000 | 10-Q | 2026-07-29 |
  | W5 | WeightedAverageNumberOfDilutedSharesOutstanding | 2026-01-01 | 2026-06-30 | 2020 | 10-Q | 2026-07-29 |

- BBB 有一次拆股：1 拆 2，除权日 2026-03-02
- Alpaca 上 BBB 在 2025-10-14 到 2026-10-13 之间只有下面几天的原始收盘价：2025-11-03 35，2026-01-30 45，2026-02-27 50，2026-03-02 25，2026-07-29 24，2026-08-03 22，2026-10-13 23.5
- SEC 的代码表里没有 CCC

#### Scenario: Fundamentals 有这个工具

- **WHEN** 调用 Fundamentals 运行 T-4
- **THEN** 请求里有读取工具 `valuation`，它没有参数
- **AND** 它的说明写明股票和时刻由程序给出，参数里没有股票代码

#### Scenario: News 没有这个工具

- **WHEN** 调用 News 运行 BBB 的新闻任务
- **THEN** 请求里没有 `valuation`

### Requirement: 价格

`valuation` 的价格 SHALL 这样取：

| 情况 | 价格 |
| --- | --- |
| 快照里有任务股票的行情，并且有最新价 | 这个最新价，写明是快照的最新价和行情的更新时刻 |
| 其他情况（行情没有最新价、快照里没有这只股票的行情） | 「历史位置」取到的原始收盘价里最后一天的收盘价，写明是哪一天的收盘价 |

#### Scenario: 用快照的最新价

- **WHEN** 调用 `valuation`
- **THEN** 价格是 23，写明是快照的最新价，更新时刻 2026-10-14 14:59:00Z

#### Scenario: 行情没有最新价

- **GIVEN** S1 里 BBB 的行情没有最新价
- **WHEN** 调用 `valuation`
- **THEN** 价格是 23.5，写明是 2026-10-13 的收盘价

#### Scenario: 寻股的候选没有行情

- **GIVEN** S1 里没有 BBB 的行情
- **WHEN** 调用 `valuation`
- **THEN** 价格是 23.5，写明是 2026-10-13 的收盘价

### Requirement: TTM

`valuation` 在某一天的 TTM EPS 和 TTM 营收 SHALL 用那一天能用的事实（「截止」）这样算：

1. 全年：时长 350 到 380 天的事实里结束日最晚的
2. 今年累计：起始日是全年结束日的下一天、时长 80 到 290 天的事实里结束日最晚的；没有时 TTM 就是全年，最近一期是全年
3. 去年同期累计：起始日和全年相同、时长和今年累计相差不超过 10 天的事实，有几条时用结束日最晚的
4. TTM 是全年加今年累计、减去年同期累计，最近一期是今年累计；有今年累计、没有去年同期累计时，算不出 TTM

- 每一项用 financials-tool spec「每个季度的数字」的规则选版本：同一个科目、单位、起始日和结束日，用提交日最晚的那个
- TTM EPS 只用 EarningsPerShareDiluted（USD/shares），每一项先按「拆股换算」换到那一天的口径再加减
- TTM 营收按 financials-tool spec「每个季度的数字」里营收的科目顺序，用第一个算得出 TTM、而且最近一期不太旧（「太旧的数字」）的科目，三项来自同一个科目

#### Scenario: 全年加今年累计减去年同期累计

- **WHEN** 调用 `valuation`
- **THEN** TTM EPS 是 1.10（E4 换算成 1.00，加 E8 的 0.60，减 E7 的 0.50），最近一期结束日 2026-06-30
- **AND** 去年同期累计用 E7，不用 E3：同一个时期用最近一次提交的版本
- **AND** TTM 营收是 4500（R4 加 R7 减 R8），最近一期结束日 2026-06-30

#### Scenario: 没有今年累计

- **GIVEN** S1 的生成时刻是 2026-03-05 15:00:00Z；S1 里 BBB 的最新价是 25
- **WHEN** 调用 `valuation`
- **THEN** TTM EPS 是 1.00（E4 换算），最近一期结束日 2025-12-31；PE 25.0
- **AND** TTM 营收是 4000（R4）

#### Scenario: 没有去年同期累计

- **GIVEN** BBB 没有 E3 和 E7
- **WHEN** 调用 `valuation`
- **THEN** 没有 TTM EPS，PE 写明没有数字，不给 PE 的位置
- **AND** PS 照常给出

#### Scenario: 去年同期的时长差 10 天

- **GIVEN** BBB 没有 E3；E7 的结束日是 2025-06-20（时长 170 天，E8 是 180 天）
- **WHEN** 调用 `valuation`
- **THEN** TTM EPS 是 1.10

#### Scenario: 去年同期的时长差 11 天

- **GIVEN** BBB 没有 E3；E7 的结束日是 2025-06-19（时长 169 天）
- **WHEN** 调用 `valuation`
- **THEN** 没有 TTM EPS

#### Scenario: 营收按科目的顺序跳过太旧的

- **GIVEN** BBB 没有 R5 到 R8；另有 RevenueFromContractWithCustomerExcludingAssessedTax 的三条事实：2025-01-01 到 2025-12-31 是 3900，10-K，提交日 2026-02-20；2026-01-01 到 2026-06-30 是 2300，2025-01-01 到 2025-06-30 是 1800，都是 10-Q，提交日 2026-07-29
- **WHEN** 调用 `valuation`
- **THEN** TTM 营收是 4400（3900 加 2300 减 1800），最近一期结束日 2026-06-30
- **AND** 没有用 Revenues：它只能算出截至 2025-12-31 的 4000，离 2026-10-14 有 287 天，太旧

### Requirement: 拆股换算

一条事实换到某一天的口径时，SHALL 用除权日晚于这条事实的提交日、不晚于那一天的每一次拆股：EPS 除以拆股比例，股数乘以拆股比例。拆股比例是新股数除以旧股数（1 拆 2 是 2，2 合 1 是 0.5）。价格用那一天的原始收盘价，不换算。

- 只用除权日不晚于时刻 T 的美东日期的拆股，MUST NOT 用以后的
- 「现在」的口径是时刻 T 的美东日期那一天的口径

#### Scenario: 拆股以前提交的 EPS 换算

- **WHEN** 调用 `valuation`
- **THEN** E4（2026-02-20 提交）按 1 拆 2 换成 1.00，TTM EPS 是 1.10，不是 2.10
- **AND** E7、E8 是拆股以后提交的，不换算

#### Scenario: 反向拆股

- **GIVEN** BBB 的拆股换成 2 合 1，除权日 2026-03-02
- **WHEN** 调用 `valuation`
- **THEN** E4 换成 4.00，TTM EPS 是 4.10，PE 5.6
- **AND** 股数还是 2000（W4 是拆股以后提交的）

#### Scenario: 快照时刻以后的拆股不用

- **GIVEN** Alpaca 上 BBB 另有一次 1 拆 2，除权日 2026-10-20
- **WHEN** 调用 `valuation`
- **THEN** 结果和基准数据的相同

### Requirement: PE、PS 和市值

`valuation` 在某一天 SHALL 这样算 PE、PS 和市值，用那一天的价格（「现在」用「价格」，历史的一天用那一天的原始收盘价）：

| 数 | 算法 | 没有的情况 |
| --- | --- | --- |
| 股数 | 稀释加权股数（WeightedAverageNumberOfDilutedSharesOutstanding）的事实里结束日最晚的；结束日相同时用时长最短的；按「拆股换算」换到那一天的口径 | 没有这个科目的事实，或者太旧 |
| PE | 价格除以 TTM EPS | 没有 TTM EPS、或者太旧；TTM EPS 不大于 0 时写「亏损」（「亏损」） |
| 市值 | 价格乘以股数 | 没有股数 |
| PS | 市值除以 TTM 营收 | 没有市值，没有 TTM 营收、或者太旧，或者 TTM 营收不大于 0 |

结果 SHALL 写出 PE、PS、市值、TTM EPS、TTM 营收和股数，后三项各带最近一期的结束日。PE 和 PS 保留一位小数，TTM EPS 保留两位，市值、TTM 营收和股数取整，都四舍五入；计算用不舍入的数。

#### Scenario: 现在的估值

- **WHEN** 调用 `valuation`
- **THEN** 股数 2000（W4，结束日 2026-06-30；W5 结束日相同、时长更长），市值 46000，PE 20.9，PS 10.2

#### Scenario: 没有股数

- **GIVEN** BBB 没有 W1 到 W5
- **WHEN** 调用 `valuation`
- **THEN** 没有股数、市值和 PS，不给 PS 的位置
- **AND** PE 是 20.9

#### Scenario: TTM 营收不大于 0

- **GIVEN** R7 的数值是 -2100
- **WHEN** 调用 `valuation`
- **THEN** TTM 营收是 0，没有 PS；市值是 46000

### Requirement: 太旧的数字

某一天的 TTM EPS、TTM 营收或股数，最近一期的结束日离那一天超过 `max_figures_age_days` 天时，这一项那一天 SHALL 不用，按没有算。结果 SHALL 写明最近一期结束超过这么多天的数字不用。

#### Scenario: 历史上太旧的一天

- **WHEN** 调用 `valuation`
- **THEN** 2026-01-30 没有 PE 和 PS：那一天能用的最近一期结束日是 2025-06-30，离那一天 214 天，计入没有数字的天数

#### Scenario: 正好到上限

- **GIVEN** `max_figures_age_days` 是 106
- **WHEN** 调用 `valuation`
- **THEN** PE 是 20.9：最近一期结束日 2026-06-30 离 2026-10-14 正好 106 天

#### Scenario: 超过上限

- **GIVEN** `max_figures_age_days` 是 105
- **WHEN** 调用 `valuation`
- **THEN** 没有 PE、股数、市值和 PS，不给 PE 和 PS 的位置；写明最近一期结束超过 105 天的数字不用

### Requirement: 亏损

TTM EPS 不大于 0 时：

- 现在的 PE SHALL 写「亏损」，MUST NOT 给 PE 的数值和 PE 的位置；PS 和市值照常给出
- 历史上 TTM EPS 不大于 0 的那一天 SHALL 不算进 PE 的位置，计入亏损的天数

#### Scenario: 现在亏损

- **GIVEN** E8 的数值是 -0.70
- **WHEN** 调用 `valuation`
- **THEN** TTM EPS 是 -0.20，PE 写「亏损」，没有 PE 的位置
- **AND** PS 是 10.2，PS 的位置和基准数据的相同

#### Scenario: 历史上亏损的一天

- **GIVEN** E1 的数值是 -0.50
- **WHEN** 调用 `valuation`
- **THEN** PE 的位置：用了 5 天，亏损 1 天（2025-11-03），没有数字 1 天；分位 20.0%，最低 20.0，中位 22.9，最高 25.0

### Requirement: 历史位置

`valuation` SHALL 给出 PE 和 PS 在这只股票自身历史里的位置：

- 窗口从时刻 T 的美东日期往前 `valuation_years` 年的同一天（2 月 29 日往前取 2 月 28 日）起，到时刻 T 的美东日期的前一天止；SHALL 向 Alpaca 取这只股票窗口里每个交易日的原始收盘价
- 窗口里有收盘价的每一天按「TTM」「拆股换算」「PE、PS 和市值」「太旧的数字」「亏损」算出那一天的 PE 和 PS，只用那一天能用的事实（「截止」）
- 每一项的位置 SHALL 给出：分位（值低于现在的天数占用了的天数的百分比）、最低、中位（天数是偶数时取中间两个的平均）、最高，用了多少天，没有数字的天数；PE 另给亏损的天数。分位、最低、中位、最高保留一位小数，四舍五入
- 用了 0 天时只给天数，不给分位、最低、中位和最高
- 现在没有 PE（没有数字或者亏损）时不给 PE 的位置；现在没有 PS 时不给 PS 的位置
- 结果 SHALL 写明窗口的起止日，以及位置是按窗口里每个交易日的原始收盘价和那一天以前提交的数字算的

#### Scenario: 基准数据的位置

- **WHEN** 调用 `valuation`
- **THEN** 窗口是 2025-10-14 到 2026-10-13；Alpaca 收到 BBB 这段日期的原始收盘价请求
- **AND** PE 的位置：用了 6 天，没有数字 1 天，亏损 0 天；分位 33.3%，最低 19.4，中位 22.1，最高 25.0
- **AND** PS 的位置：用了 6 天，没有数字 1 天；分位 33.3%，最低 9.8，中位 11.1，最高 13.0

#### Scenario: 拆股那天不跳

- **WHEN** 调用 `valuation`
- **THEN** 2026-02-27 和 2026-03-02 的 PE 都是 25.0、PS 都是 13.0：2026-03-02 的 E4 换成 1.00，W2 换成 2080

#### Scenario: 每一天只用那一天以前提交的

- **WHEN** 调用 `valuation`
- **THEN** 2026-07-29 的 TTM EPS 是 1.05（E4 换算成 1.00，加 E6，减 E5），不用当天提交的 E7、E8

#### Scenario: 一天也用不上

- **GIVEN** Alpaca 上 BBB 在窗口里只有 2026-01-30 的原始收盘价
- **WHEN** 调用 `valuation`
- **THEN** PE 和 PS 的位置都是用了 0 天、没有数字 1 天，没有分位、最低、中位和最高
- **AND** PE 是 20.9

#### Scenario: 窗口的年数

- **GIVEN** `valuation_years` 是 2
- **WHEN** 调用 `valuation`
- **THEN** Alpaca 收到的原始收盘价请求是 2024-10-14 到 2026-10-13

#### Scenario: 2 月 29 日

- **GIVEN** S1 的生成时刻是 2028-02-29 15:00:00Z
- **WHEN** 调用 `valuation`
- **THEN** 窗口从 2027-02-28 起

### Requirement: 截止

`valuation` 在某一天 SHALL 只用提交日早于那一天的事实：「现在」是时刻 T 的美东日期，历史的一天是那个交易日；提交日是 SEC 给出的美东日期。结果 SHALL 写明现在的数字只用了时刻 T 的美东日期以前提交的。

#### Scenario: 当天提交的不用

- **WHEN** 调用 `valuation`
- **THEN** 不用 E9、E10（2026-10-14 提交）；写明只用了 2026-10-14 以前提交的数字

#### Scenario: 美东日期

- **GIVEN** S1 的生成时刻是 2026-10-15 02:00:00Z（美东还是 2026-10-14）
- **WHEN** 调用 `valuation`
- **THEN** 仍然不用 E9、E10

#### Scenario: 第二天就能用

- **GIVEN** S1 的生成时刻是 2026-10-15 14:00:00Z
- **WHEN** 调用 `valuation`
- **THEN** TTM EPS 是 1.20（E4 换算成 1.00，加 E9，减 E10），最近一期结束日 2026-09-30；PE 19.2

### Requirement: 补取和未覆盖

每次调用 `valuation`，SHALL 先按 financials-tool spec「补取」向 SEC 取这只股票的财务事实、存下没存过的，再从状态库读出来算。

- SEC 这次不可用时，SHALL 用状态库里已经存下的，并写明 SEC 这次不可用、数字可能不全
- 按 financials-tool spec「补取」算未覆盖时，SHALL 写明这只股票未覆盖，不给价格和别的数，MUST NOT 向 Alpaca 请求

#### Scenario: 第一次调用存下事实

- **GIVEN** 状态库里没有 BBB 的财务事实
- **WHEN** 调用 `valuation`
- **THEN** SEC 收到 BBB 的请求，状态库存下 BBB 的财务事实
- **AND** 结果和上面各场景相同

#### Scenario: SEC 不可用

- **GIVEN** 之前已经存下 E1 到 W5 的全部事实；SEC 这次报数据源不可用
- **WHEN** 调用 `valuation`
- **THEN** 结果和基准数据的相同，另外写明 SEC 这次不可用、数字可能不全

#### Scenario: SEC 没有这只股票

- **GIVEN** T-4 的股票换成 CCC
- **WHEN** 调用 `valuation`
- **THEN** 写明 CCC 未覆盖，没有价格和别的数
- **AND** Alpaca 没有收到请求

#### Scenario: 一个季度也列不出来

- **GIVEN** SEC 上 BBB 只有 E9
- **WHEN** 调用 `valuation`
- **THEN** 写明 BBB 未覆盖；Alpaca 没有收到请求

### Requirement: Alpaca 的请求和不可用

没有未覆盖时，每次调用 `valuation` SHALL 向 Alpaca 发两个请求：窗口里的原始收盘价（「历史位置」），和除权日不晚于时刻 T 的美东日期的拆股记录（alpaca-bars spec「拆股记录」）。

任何一个报数据源不可用时，SHALL 写明 Alpaca 这次不可用、估值这次给不出，不给价格、PE、PS、市值和位置；财务事实照常存下。

#### Scenario: 两个请求

- **WHEN** 调用 `valuation`
- **THEN** Alpaca 收到 BBB 2025-10-14 到 2026-10-13 的原始收盘价请求，和 BBB 除权日不晚于 2026-10-14 的拆股记录请求

#### Scenario: 收盘价不可用

- **GIVEN** Alpaca 的原始收盘价请求报数据源不可用
- **WHEN** 调用 `valuation`
- **THEN** 写明 Alpaca 这次不可用、估值这次给不出；没有价格、PE、PS、市值和位置
- **AND** 状态库存下了 BBB 的财务事实

#### Scenario: 拆股记录不可用

- **GIVEN** Alpaca 的拆股记录请求报数据源不可用
- **WHEN** 调用 `valuation`
- **THEN** 写明 Alpaca 这次不可用、估值这次给不出；没有价格、PE、PS、市值和位置

### Requirement: 结果的说明

有价格时，结果 SHALL 写明：

- TTM 是上个财年全年加今年累计、减去年同期累计
- 每股数字和股数已经按拆股换到同一个口径
- 财报当天的新一季要等 10-Q 提交以后才算进 TTM
- 「价格」「太旧的数字」「历史位置」「截止」要求写明的内容

#### Scenario: 写明算法和口径

- **WHEN** 调用 `valuation`
- **THEN** 结果写明 TTM 的算法、拆股的口径、新一季要等 10-Q、价格的来源、最近一期结束超过 200 天的数字不用、窗口的起止日和位置的算法，以及只用了 2026-10-14 以前提交的数字

### Requirement: 结果可以引用

`valuation` 的结果里有价格时，条目 `valuation/<股票代码>` SHALL 算这次运行的工具返回过的内容（sub-agent-calls spec「工具返回过的内容」）；没有价格时 MUST NOT 算。

#### Scenario: 结果可以引用

- **GIVEN** 测试服务器第一次回答调用 `valuation`；第二次回答调用 `submit_signal`，引用 `valuation/BBB`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 完成

#### Scenario: 未覆盖不能引用

- **GIVEN** T-4 的股票换成 CCC；测试服务器第一次回答调用 `valuation`；第二次回答调用 `submit_signal`，引用 `valuation/CCC`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 失败，原因是格式不合法，说明写明未知的条目 `valuation/CCC`
