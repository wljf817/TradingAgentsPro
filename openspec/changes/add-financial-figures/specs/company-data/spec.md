## ADDED Requirements

### Requirement: 财务事实的记录

一条财务事实 SHALL 是公司在一份提交给 SEC 的文件里报告的一个时期的数：

| 字段 | 说明 |
| --- | --- |
| 股票 | 股票代码 |
| 科目 | us-gaap 的科目名，比如 Revenues |
| 单位 | 比如 USD、USD/shares、shares |
| 起始日、结束日 | 这个数所属时期的第一天和最后一天 |
| 数值 | 十进制精确保存数据源给出的值 |
| accession number | 报告它的那份文件 |
| 表单类型 | 那份文件的表单，比如 10-Q |
| 提交日 | 那份文件的提交日，数据源给出的美东日期 |

同一个股票、科目、单位、起始日、结束日和 accession number SHALL 是同一条事实。同一个时期在不同文件里报告的数是不同的事实，都保留。

公司数据模块 SHALL 只取下面这些科目：Revenues、RevenuesNetOfInterestExpense、RevenueFromContractWithCustomerExcludingAssessedTax、GrossProfit、OperatingIncomeLoss、NetIncomeLoss、EarningsPerShareDiluted、WeightedAverageNumberOfDilutedSharesOutstanding、NetCashProvidedByUsedInOperatingActivities、PaymentsToAcquirePropertyPlantAndEquipment、PaymentsToAcquireProductiveAssets。

#### Scenario: 同一个时期在两份文件里

- **GIVEN** 数据源上 AAA 的 Revenues 2025-04-01 到 2025-06-30 有两条：1000，10-Q，提交日 2025-07-30，accession 0000000001-25-000012；1040，10-Q，提交日 2026-07-29，accession 0000000001-26-000003
- **WHEN** 取 AAA 的财务事实，再读 AAA 的财务事实
- **THEN** 两条都读得到

### Requirement: 取一只股票的财务事实

取一只股票的财务事实 SHALL 向数据源要这只股票上面列的科目的全部事实，存下状态库里还没有的。已经有的 MUST NOT 改动，数据源这次没有给出的 MUST NOT 删除。

- 数据源没有这只股票的财务事实时，SHALL 给出没有，什么都不存
- 数据源不可用时 SHALL 报数据源不可用，什么都不存

#### Scenario: 第一次取

- **GIVEN** 状态库里没有 AAA 的财务事实；数据源上 AAA 有 3 条
- **WHEN** 取 AAA 的财务事实
- **THEN** 状态库存下这 3 条

#### Scenario: 再取只多存新的

- **GIVEN** 已经取过 AAA，存下 3 条；数据源上 AAA 多了 1 条
- **WHEN** 再取 AAA 的财务事实
- **THEN** 状态库里有 4 条，原来的 3 条不变

#### Scenario: 数据源没有这只股票

- **WHEN** 取 CCC 的财务事实，数据源回答没有
- **THEN** 给出没有；状态库里没有 CCC 的财务事实

#### Scenario: 数据源不可用

- **GIVEN** 数据源报数据源不可用
- **WHEN** 取 AAA 的财务事实
- **THEN** 报数据源不可用，什么都不存

### Requirement: 读取财务事实

按股票和一个美东日期读财务事实 SHALL 给出状态库里这只股票提交日早于这一天的全部事实。读取只读状态库，MUST NOT 向数据源请求。

#### Scenario: 按提交日截止

- **GIVEN** 状态库里 AAA 有两条事实，提交日分别是 2026-10-13 和 2026-10-14
- **WHEN** 按 2026-10-14 读 AAA 的财务事实
- **THEN** 只有提交日 2026-10-13 的那一条
- **AND** 数据源没有收到请求

### Requirement: 财务事实的存储

财务事实 SHALL 存在状态库里（state-store spec），只由公司数据模块写入，只追加不覆盖。

- 读回的事实 SHALL 和存下的完全相同：每一位小数、日期
- 两个调用同时存同一条事实时，SHALL 只存一条，两个调用都不报错
- 用同一个数据库新建存储时（相当于进程重启），SHALL 读到之前存下的全部事实
- 数据库连不上、表不存在、存着的记录读不出来时，读取和保存 SHALL 报错，MUST NOT 当作没有记录

#### Scenario: 原样读回

- **GIVEN** 数据源上 AAA 的 EarningsPerShareDiluted 2026-04-01 到 2026-06-30 是 0.5200
- **WHEN** 取 AAA 的财务事实，再读回
- **THEN** 数值正好是 0.5200

#### Scenario: 同时存同一条

- **WHEN** 两个调用同时取 AAA 的财务事实，数据源给出同样的 3 条
- **THEN** 状态库里有 3 条，两个调用都没有报错

#### Scenario: 重启后读到同样的事实

- **GIVEN** 已经取过 AAA，存下 3 条
- **WHEN** 用同一个数据库新建存储，读 AAA 的财务事实
- **THEN** 读到同样的 3 条

#### Scenario: 数据库连不上时报错

- **GIVEN** 数据库连不上
- **WHEN** 取 AAA 的财务事实；读 AAA 的财务事实
- **THEN** 两个操作都报错，读取不返回空

### Requirement: 按时刻读财报

按股票和时刻 T 读财报 SHALL 用状态库里写入时刻早于 T 的版本（state-store spec「读取某个时刻的版本」）：

- 财报日历：每次发布在 T 以前的最新版本，按发布日从早到晚；T 以前还没有版本的发布没有
- surprise：T 以前最近一次保存的那一组，按报告期从新到旧；T 以前没有保存过时为没有

读取只读状态库，MUST NOT 向数据源请求。

#### Scenario: 按时刻读财报日历

- **GIVEN** AAA 2027 财年第 2 季度 2026-10-14 15:00Z 记下发布日 2026-12-17，2026-12-01 15:00Z 记下发布日 2026-12-18；2027 财年第 3 季度 2026-12-01 15:00Z 第一次记下
- **WHEN** 按 2026-11-01 00:00Z 读 AAA 的财报日历
- **THEN** 只有 2027 财年第 2 季度，发布日 2026-12-17

#### Scenario: 按时刻读 surprise

- **GIVEN** AAA 的 surprise 2026-10-14 15:00Z 第一次保存
- **WHEN** 按 2026-10-14 15:00Z 读 AAA 的 surprise
- **THEN** 没有
- **WHEN** 按 2026-10-14 16:00Z 读
- **THEN** 是那一组

## MODIFIED Requirements

### Requirement: 公司数据配置

公司数据模块的配置项 SHALL 可配置，默认值如下：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| 窗口往前天数 | 30 | 刷新财报日历时，取当前 UTC 日期往前这么多天以来的发布 |
| 窗口往后天数 | 90 | 刷新财报日历时，取到当前 UTC 日期往后这么多天为止的发布 |
| `financial_quarters` | 8 | `financials` 最多列多少个季度、多少次财报发布（financials-tool spec） |

三项都 SHALL 是大于 0 的整数，否则配置报错。

#### Scenario: 默认配置

- **WHEN** 不传任何配置项新建配置
- **THEN** 窗口往前 30 天，往后 90 天

#### Scenario: 改窗口

- **GIVEN** 基准数据，窗口往前 10 天、往后 60 天
- **WHEN** 刷新 AAA
- **THEN** 财报日历的请求是发布日在 2026-10-04 到 2026-12-13 之间

#### Scenario: 配置非法

- **WHEN** 窗口往前天数为 0，或者窗口往后天数为 -1，或者 `financial_quarters` 为 0
- **THEN** 配置报错
