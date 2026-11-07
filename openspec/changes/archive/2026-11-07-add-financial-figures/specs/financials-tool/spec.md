# Spec Delta

## Purpose

Fundamentals 的读取工具 `financials`：按财季列出任务股票的营收、利润、EPS 和现金流，由代码算好增长率和利润率，并给出一致预期和实际值。只用快照时刻以前提交的数字，缺的向 SEC 和 Finnhub 补取并存下。

## ADDED Requirements

### Requirement: 财务数字工具

向真实模型调用 Fundamentals 时，SHALL 给出读取工具 `financials`（model-adapter spec「读取工具」），作用是列出任务股票的季度财务数字和一致预期。

- `financials` MUST NOT 有参数：股票是任务的股票，时刻是这次运行读的这版快照的生成时刻，由程序给出
- 工具的说明 SHALL 写明股票和时刻由程序给出，参数里没有股票代码
- 工具 SHALL 只读：除了「补取」存下财务事实、「一致预期和实际值」刷新财报，不改变任何记录
- 工具次数按 sub-agent-calls spec「工具次数」算，和公告工具合在一起
- News、Quant、PM、Critic 和 Reviewer SHALL NOT 有这个工具；替身不查询

本 spec 的场景除非另有说明，都使用下面的基准数据：

- T-4 是 Fundamentals 的任务，股票 BBB，读 S1；S1 的生成时刻是 2026-10-14 15:00:00Z（美东 2026-10-14），时刻 T 指这个时刻
- 季度数（company-data spec「公司数据配置」的 `financial_quarters`）是 5；没有写明的其他配置都是默认配置
- SEC 上 BBB 的财务事实如下，accession number 写后三位（全称 0000000002-25-000011 写 25-011），金额单位是美元，EPS 是美元每股：

  | 事实 | 科目 | 起始日 | 结束日 | 数值 | 表单 | 提交日 | accession |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | F1 | Revenues | 2025-01-01 | 2025-03-31 | 900 | 10-Q | 2025-04-30 | 25-011 |
  | F2 | Revenues | 2025-04-01 | 2025-06-30 | 1000 | 10-Q | 2025-07-30 | 25-012 |
  | F3 | Revenues | 2025-07-01 | 2025-09-30 | 1100 | 10-Q | 2025-10-29 | 25-013 |
  | F4 | Revenues | 2025-01-01 | 2025-09-30 | 3000 | 10-Q | 2025-10-29 | 25-013 |
  | F5 | Revenues | 2025-01-01 | 2025-12-31 | 4200 | 10-K | 2026-02-20 | 26-001 |
  | F6 | Revenues | 2026-01-01 | 2026-03-31 | 1150 | 10-Q | 2026-04-29 | 26-002 |
  | F7 | Revenues | 2026-04-01 | 2026-06-30 | 1300 | 10-Q | 2026-07-29 | 26-003 |
  | F8 | Revenues | 2025-04-01 | 2025-06-30 | 1040 | 10-Q | 2026-07-29 | 26-003 |
  | F9 | Revenues | 2026-07-01 | 2026-09-30 | 1400 | 10-Q | 2026-10-14 | 26-004 |
  | F10 | GrossProfit | 2026-04-01 | 2026-06-30 | 520 | 10-Q | 2026-07-29 | 26-003 |
  | F11 | NetIncomeLoss | 2026-04-01 | 2026-06-30 | 130 | 10-Q | 2026-07-29 | 26-003 |
  | F12 | NetIncomeLoss | 2025-04-01 | 2025-06-30 | 100 | 10-Q | 2026-07-29 | 26-003 |
  | F13 | EarningsPerShareDiluted | 2026-04-01 | 2026-06-30 | 0.52 | 10-Q | 2026-07-29 | 26-003 |
  | F14 | EarningsPerShareDiluted | 2025-04-01 | 2025-06-30 | 0.40 | 10-Q | 2026-07-29 | 26-003 |
  | F15 | EarningsPerShareDiluted | 2025-01-01 | 2025-12-31 | 1.80 | 10-K | 2026-02-20 | 26-001 |
  | F16 | EarningsPerShareDiluted | 2025-01-01 | 2025-09-30 | 1.30 | 10-Q | 2025-10-29 | 25-013 |
  | F17 | NetCashProvidedByUsedInOperatingActivities | 2026-01-01 | 2026-03-31 | 300 | 10-Q | 2026-04-29 | 26-002 |
  | F18 | NetCashProvidedByUsedInOperatingActivities | 2026-01-01 | 2026-06-30 | 700 | 10-Q | 2026-07-29 | 26-003 |
  | F19 | PaymentsToAcquirePropertyPlantAndEquipment | 2026-01-01 | 2026-03-31 | 100 | 10-Q | 2026-04-29 | 26-002 |
  | F20 | PaymentsToAcquirePropertyPlantAndEquipment | 2026-01-01 | 2026-06-30 | 250 | 10-Q | 2026-07-29 | 26-003 |
  | F21 | WeightedAverageNumberOfDilutedSharesOutstanding | 2026-04-01 | 2026-06-30 | 250 | 10-Q | 2026-07-29 | 26-003 |

- 状态库里 BBB 的财报（company-data spec），记下的时刻都早于时刻 T：
  - 2026 财年第 2 季度：发布日 2026-07-28，盘后；EPS 预期 0.50、实际 0.55；营收预期 1250、实际 1300
  - 2026 财年第 3 季度：发布日 2026-10-27，没写发布时段；EPS 预期 0.58、没有实际值；营收预期 1380、没有实际值
  - surprise：报告期 2026-06-30，2026 财年第 2 季度，EPS 实际 0.55、预期 0.50，surprise 0.05，surprise 百分比 10.0
- SEC 的代码表里没有 CCC；Finnhub 上 CCC 的财报日历和 surprise 都是空的

#### Scenario: Fundamentals 有这个工具

- **WHEN** 调用 Fundamentals 运行 T-4
- **THEN** 请求里有读取工具 `financials`，它没有参数
- **AND** 它的说明写明股票和时刻由程序给出，参数里没有股票代码

#### Scenario: News 没有这个工具

- **WHEN** 调用 News 运行 BBB 的新闻任务
- **THEN** 请求里没有 `financials`

### Requirement: 列出的季度

`financials` SHALL 按财季列出任务股票最近的季度，最新的在前，最多 `financial_quarters` 个：

- 季度的结束日是这只股票各项科目（「每个季度的数字」的表）里能用的事实中，时长约 3 个月（80 到 100 天）或约一年（350 到 380 天）的事实的结束日；时长是结束日减起始日
- 季度的起始日是前一个季度结束日的下一天；最早一个结束日前面没有结束日，不列这个季度
- 能用的事实是「截止」以内的事实

#### Scenario: 基准数据的季度

- **WHEN** 调用 `financials`
- **THEN** 依次列出 2026-04-01 到 2026-06-30、2026-01-01 到 2026-03-31、2025-10-01 到 2025-12-31、2025-07-01 到 2025-09-30、2025-04-01 到 2025-06-30 五个季度

#### Scenario: 最早的结束日不列

- **GIVEN** `financial_quarters` 是 8
- **WHEN** 调用 `financials`
- **THEN** 仍然只列出上面五个季度
- **AND** 没有 2025-01-01 到 2025-03-31：2025-03-31 前面没有结束日

#### Scenario: 季度数是上限

- **GIVEN** `financial_quarters` 是 2
- **WHEN** 调用 `financials`
- **THEN** 只列出 2026-04-01 到 2026-06-30、2026-01-01 到 2026-03-31

### Requirement: 每个季度的数字

每个季度 SHALL 给出下表的各项。有几个科目的项，按表里的顺序用第一个有数的科目，一个季度的数只来自一个科目：

| 项 | 科目（us-gaap） | 单位 |
| --- | --- | --- |
| 营收 | Revenues、RevenuesNetOfInterestExpense、RevenueFromContractWithCustomerExcludingAssessedTax | USD |
| 毛利 | GrossProfit | USD |
| 营业利润 | OperatingIncomeLoss | USD |
| 净利润 | NetIncomeLoss | USD |
| 稀释 EPS | EarningsPerShareDiluted | USD/shares |
| 稀释加权股数 | WeightedAverageNumberOfDilutedSharesOutstanding | shares |
| 经营现金流 | NetCashProvidedByUsedInOperatingActivities | USD |
| 资本开支 | PaymentsToAcquirePropertyPlantAndEquipment、PaymentsToAcquireProductiveAssets | USD |

一个科目在一个季度的数 SHALL 这样取：

1. 有起始日和结束日正好是这个季度的事实，用它
2. 否则，金额（单位是 USD 的项）用两个累计数相减：起始日相同、结束日分别是这个季度的结束日和前一个季度的结束日的两个事实，前者减后者；有几组时用起始日最晚的一组。结果写明是相减得到的
3. 都没有，这个科目在这个季度没有数

稀释 EPS 和稀释加权股数 MUST NOT 相减。全部科目都没有数的项写明缺失，MUST NOT 估算。

同一个科目、单位、起始日和结束日有几个版本时，SHALL 用「截止」以内提交日最晚的那个；提交日相同时用 accession number 大的那个。别的单位的事实不用。

#### Scenario: 直接报告的季度

- **WHEN** 调用 `financials`
- **THEN** 2026-04-01 到 2026-06-30：营收 1300，毛利 520，营业利润缺失，净利润 130，稀释 EPS 0.52，稀释加权股数 250
- **AND** 营收不是相减得到的

#### Scenario: 用最近一次提交的版本

- **WHEN** 调用 `financials`
- **THEN** 2025-04-01 到 2025-06-30 的营收是 1040（F8），不是 1000（F2）

#### Scenario: 第四季度是全年减前三季度

- **WHEN** 调用 `financials`
- **THEN** 2025-10-01 到 2025-12-31 的营收是 1200（F5 减 F4），写明是相减得到的
- **AND** 稀释 EPS 缺失：F15 减 F16 不算

#### Scenario: 现金流的累计数相减

- **WHEN** 调用 `financials`
- **THEN** 2026-04-01 到 2026-06-30 的经营现金流是 400，资本开支是 150，都写明是相减得到的
- **AND** 2026-01-01 到 2026-03-31 的经营现金流是 300，资本开支是 100，不是相减得到的

#### Scenario: 按科目的顺序取

- **GIVEN** BBB 另有 RevenueFromContractWithCustomerExcludingAssessedTax 的事实：2026-04-01 到 2026-06-30 是 1250，2025-07-01 到 2025-09-30 是 1080；而且没有 F3
- **WHEN** 调用 `financials`
- **THEN** 2026-04-01 到 2026-06-30 的营收是 1300（Revenues）
- **AND** 2025-07-01 到 2025-09-30 的营收是 1080（RevenueFromContractWithCustomerExcludingAssessedTax）：Revenues 只能用 F4 减 2025-01-01 到 2025-06-30 的累计数，而这个累计数没有

#### Scenario: 公司没有报告的项

- **WHEN** 调用 `financials`
- **THEN** 2025-07-01 到 2025-09-30 只有营收 1100，其余各项都写明缺失

### Requirement: 代码算的数

每个季度 SHALL 另外给出下面这些由代码算的数，算不了时写明没有：

| 数 | 算法 | 算不了的情况 |
| --- | --- | --- |
| 自由现金流 | 经营现金流减资本开支 | 两项有一项缺失 |
| 毛利率、营业利润率、净利率 | 毛利、营业利润、净利润除以营收 | 分子缺失，或营收缺失、不大于 0 |
| 营收、营业利润、净利润、稀释 EPS 的同比增长 | 这个季度的数除以上年同期的数再减 1；上年同期是列表里往后数第 4 个季度 | 上年同期不在列表里，或者两个数有一个缺失，或者上年同期的数不大于 0 |

比率和增长 SHALL 写成百分数，保留一位小数，四舍五入。

#### Scenario: 利润率和自由现金流

- **WHEN** 调用 `financials`
- **THEN** 2026-04-01 到 2026-06-30：毛利率 40.0%，净利率 10.0%，营业利润率没有；自由现金流 250
- **AND** 2026-01-01 到 2026-03-31：自由现金流 200，三个利润率都没有

#### Scenario: 同比增长

- **WHEN** 调用 `financials`
- **THEN** 2026-04-01 到 2026-06-30：营收同比增长 25.0%（1300 比 1040），净利润 30.0%，稀释 EPS 30.0%，营业利润没有
- **AND** 2026-01-01 到 2026-03-31 没有同比增长：上年同期 2025-01-01 到 2025-03-31 不在列表里

#### Scenario: 上年同期亏损

- **GIVEN** F12 的数值是 -20
- **WHEN** 调用 `financials`
- **THEN** 2026-04-01 到 2026-06-30 的净利润同比增长写明没有

### Requirement: 截止

`financials` SHALL 只用提交日早于时刻 T 的美东日期的事实；提交日是 SEC 给出的美东日期。时刻 T 那一天提交的事实 MUST NOT 用。结果 SHALL 写明只用了这一天以前提交的数字。

#### Scenario: 当天提交的不用

- **WHEN** 调用 `financials`
- **THEN** 没有 2026-07-01 到 2026-09-30 这个季度（F9 是 2026-10-14 提交的）
- **AND** 结果写明只用了 2026-10-14 以前提交的数字

#### Scenario: 美东日期

- **GIVEN** S1 的生成时刻是 2026-10-15 02:00:00Z（美东还是 2026-10-14）
- **WHEN** 调用 `financials`
- **THEN** 仍然不用 F9

#### Scenario: 第二天就能用

- **GIVEN** S1 的生成时刻是 2026-10-15 14:00:00Z
- **WHEN** 调用 `financials`
- **THEN** 第一个季度是 2026-07-01 到 2026-09-30，营收 1400

### Requirement: 补取

每次调用 `financials`，SHALL 先向 SEC 取这只股票的财务事实，存下没存过的（company-data spec「取一只股票的财务事实」），再从状态库读出这只股票的财务事实来算。

- SEC 这次不可用时，SHALL 用状态库里已经存下的，并写明 SEC 这次不可用、季度数字可能不全
- SEC 没有这只股票的财务事实（代码表里没有，或者 SEC 回答没有），或者这次取到了、但「截止」以内一个季度也列不出来时，SHALL 写明这只股票未覆盖，不列季度；一致预期照常给出

#### Scenario: 第一次调用存下事实

- **GIVEN** 状态库里没有 BBB 的财务事实
- **WHEN** 调用 `financials`
- **THEN** SEC 收到 BBB 的请求；状态库存下 F1 到 F21
- **AND** 结果和上面各场景相同

#### Scenario: SEC 不可用

- **GIVEN** 之前已经存下 F1 到 F8；SEC 这次报数据源不可用
- **WHEN** 调用 `financials`
- **THEN** 列出五个季度，只有营收；写明 SEC 这次不可用、季度数字可能不全

#### Scenario: SEC 不可用，也没存过

- **GIVEN** 状态库里没有 BBB 的财务事实；SEC 这次报数据源不可用
- **WHEN** 调用 `financials`
- **THEN** 没有季度；写明 SEC 这次不可用、季度数字可能不全，不写未覆盖

#### Scenario: SEC 没有这只股票

- **GIVEN** T-4 的股票换成 CCC
- **WHEN** 调用 `financials`
- **THEN** 写明 CCC 未覆盖，没有季度

#### Scenario: 一个季度也列不出来

- **GIVEN** SEC 上 BBB 只有 F9
- **WHEN** 调用 `financials`
- **THEN** 写明 BBB 未覆盖，没有季度
- **AND** 一致预期照常给出

### Requirement: 一致预期和实际值

`financials` SHALL 给出这只股票的一致预期和实际值，来自公司数据模块（company-data spec「按时刻读财报」）：

- 财报发布：每次发布在时刻 T 以前记下的最新版本，按发布日从晚到早，最多 `financial_quarters` 次；每次给出财年、季度、发布日、发布时段、EPS 预期和实际、营收预期和实际
- 每次发布由代码算出 EPS 和营收的超预期幅度：实际减预期，除以预期的绝对值；没有实际值、预期为空或为 0 时写明没有；写成百分数，保留一位小数，四舍五入
- 下一次发布：发布日不早于时刻 T 的美东日期的最早一次；没有时写明没有
- surprise：时刻 T 以前记下的最近一组，按报告期从新到旧
- SHALL 写明一致预期和实际值来自 Finnhub，EPS 可能是调整后的口径，和公司报告的稀释 EPS 不同

时刻 T 以前这只股票的财报发布和 surprise 都没有记下过时，SHALL 先刷新这只股票的财报（company-data spec「刷新一只股票的财报」），再用刷新以后的最新版本，并写明这只股票时刻 T 以前没有记下过一致预期、下面的是刷新时取的。刷新时 Finnhub 不可用，SHALL 写明一致预期这次取不到，季度数字照常给出。

#### Scenario: 财报发布和超预期幅度

- **WHEN** 调用 `financials`
- **THEN** 财报发布依次是 2026 财年第 3 季度（2026-10-27）、第 2 季度（2026-07-28）
- **AND** 第 2 季度：EPS 超预期 10.0%，营收超预期 4.0%；第 3 季度的超预期幅度写明没有
- **AND** 下一次发布是 2026 财年第 3 季度，发布日 2026-10-27
- **AND** surprise 是报告期 2026-06-30 一个季度；写明来自 Finnhub、EPS 口径可能不同

#### Scenario: 用时刻 T 以前记下的版本

- **GIVEN** 2026-10-14 21:00:00Z 又记下了 2026 财年第 3 季度的新版本，EPS 预期 0.60
- **WHEN** 调用 `financials`
- **THEN** 第 3 季度的 EPS 预期是 0.58

#### Scenario: 没记下过时先刷新

- **GIVEN** 状态库里没有 BBB 的财报；Finnhub 上 BBB 的财报日历和 surprise 和基准数据相同
- **WHEN** 调用 `financials`
- **THEN** Finnhub 收到 BBB 的财报日历和 surprise 的请求，状态库存下取到的记录
- **AND** 财报发布只有 2026 财年第 3 季度：第 2 季度的发布日不在刷新的窗口里（company-data spec「刷新一只股票的财报」）
- **AND** 下一次发布是 2026 财年第 3 季度；surprise 是报告期 2026-06-30 一个季度
- **AND** 写明 BBB 时刻 T 以前没有记下过一致预期，下面的是刷新时取的

#### Scenario: 时刻 T 以后才记下的也算没记下过

- **GIVEN** BBB 的财报和 surprise 都是 2026-10-14 16:00:00Z 才第一次记下的
- **WHEN** 调用 `financials`
- **THEN** 先刷新 BBB 的财报，再用刷新以后的最新版本

#### Scenario: 刷新时 Finnhub 不可用

- **GIVEN** 状态库里没有 BBB 的财报；Finnhub 报数据源不可用
- **WHEN** 调用 `financials`
- **THEN** 写明一致预期这次取不到；季度数字照常列出

#### Scenario: Finnhub 上没有

- **GIVEN** T-4 的股票换成 CCC
- **WHEN** 调用 `financials`
- **THEN** 先刷新 CCC 的财报；财报发布为空，下一次发布写明没有，surprise 为空

### Requirement: 每股数字的口径

列出季度时，结果 SHALL 写明：每股数字是各期最近一次提交的口径，拆股前后的不能直接比较，看稀释加权股数。

#### Scenario: 写明每股数字的口径

- **WHEN** 调用 `financials`
- **THEN** 结果写明每股数字的口径和拆股的影响

### Requirement: 结果可以引用

`financials` 的结果里有至少一个季度、一次财报发布或者一个 surprise 的季度时，条目 `financials/<股票代码>` SHALL 算这次运行的工具返回过的内容（sub-agent-calls spec「工具返回过的内容」）。三样都没有时 MUST NOT 算。

#### Scenario: 结果可以引用

- **GIVEN** 测试服务器第一次回答调用 `financials`；第二次回答调用 `submit_signal`，引用 `financials/BBB`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 完成

#### Scenario: 什么都没有不能引用

- **GIVEN** T-4 的股票换成 CCC；测试服务器第一次回答调用 `financials`；第二次回答调用 `submit_signal`，引用 `financials/CCC`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 失败，原因是格式不合法，说明写明未知的条目 `financials/CCC`
