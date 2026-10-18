# company-data Specification

## Purpose

公司数据模块提供两样按公司取的数据：一只股票属于哪个板块（11 个 SPDR 板块 ETF 之一），以及财报日历（含一致预期和实际值）和最近几个季度的 EPS surprise。财报存进共享状态库，只由这个模块写入。

## Requirements

### Requirement: 板块

板块 SHALL 是 11 个 SPDR 板块 ETF 之一，板块的值就是 ETF 的代码。数据源给出的行业 SHALL 按下表对应到板块；比较时去掉行业首尾的空白，不分大小写。表里没有的行业没有板块。

| 板块 | 含义 | 数据源的行业 |
| --- | --- | --- |
| XLK | 科技 | Technology、Semiconductors、Communications |
| XLC | 通信服务 | Media、Telecommunication |
| XLY | 可选消费 | Retail、Automobiles、Auto Components、Hotels, Restaurants & Leisure、Textiles, Apparel & Luxury Goods、Leisure Products、Diversified Consumer Services、Distributors |
| XLP | 必需消费 | Consumer products、Beverages、Food Products、Tobacco |
| XLV | 医疗 | Pharmaceuticals、Biotechnology、Health Care、Life Sciences Tools & Services |
| XLF | 金融 | Banking、Financial Services、Insurance |
| XLE | 能源 | Energy |
| XLI | 工业 | Aerospace & Defense、Airlines、Building、Commercial Services & Supplies、Construction、Electrical Equipment、Industrial Conglomerates、Logistics & Transportation、Machinery、Marine、Professional Services、Road & Rail、Trading Companies & Distributors、Transportation Infrastructure |
| XLB | 材料 | Chemicals、Metals & Mining、Packaging、Paper & Forest |
| XLU | 公用事业 | Utilities |
| XLRE | 房地产 | Real Estate |

#### Scenario: 科技

- **WHEN** 行业分别是 Technology、Semiconductors、Communications
- **THEN** 板块都是 XLK

#### Scenario: 通信服务

- **WHEN** 行业分别是 Media、Telecommunication
- **THEN** 板块都是 XLC

#### Scenario: 可选消费

- **WHEN** 行业分别是 Retail、Hotels, Restaurants & Leisure
- **THEN** 板块都是 XLY

#### Scenario: 必需消费

- **WHEN** 行业分别是 Beverages、Consumer products
- **THEN** 板块都是 XLP

#### Scenario: 医疗

- **WHEN** 行业分别是 Pharmaceuticals、Life Sciences Tools & Services
- **THEN** 板块都是 XLV

#### Scenario: 金融

- **WHEN** 行业分别是 Banking、Financial Services、Insurance
- **THEN** 板块都是 XLF

#### Scenario: 能源

- **WHEN** 行业是 Energy
- **THEN** 板块是 XLE

#### Scenario: 工业

- **WHEN** 行业分别是 Machinery、Logistics & Transportation
- **THEN** 板块都是 XLI

#### Scenario: 材料

- **WHEN** 行业分别是 Chemicals、Metals & Mining
- **THEN** 板块都是 XLB

#### Scenario: 公用事业

- **WHEN** 行业是 Utilities
- **THEN** 板块是 XLU

#### Scenario: 房地产

- **WHEN** 行业是 Real Estate
- **THEN** 板块是 XLRE

#### Scenario: 首尾空白和大小写不影响

- **WHEN** 行业分别是 "Energy "（末尾有空格）、" semiconductors"、"REAL ESTATE"
- **THEN** 板块分别是 XLE、XLK、XLRE

#### Scenario: 表里没有的行业

- **WHEN** 行业分别是 N/A、Shell Companies、空字符串
- **THEN** 都没有板块

### Requirement: 查一只股票的板块

查板块 SHALL 每次向数据源取这只股票的行业，按「板块」对应，不保存结果：

- 数据源查不到这只股票，或者没有给出行业：没有板块
- 行业不在代码表里：没有板块，并写一条 WARNING 日志（bootstrap spec「日志」），写明股票代码和数据源给出的行业
- 数据源不可用（超时、连不上、限频、服务端出错）：报数据源不可用，MUST NOT 当作没有板块

#### Scenario: 查到板块

- **GIVEN** 数据源上 AAA 的行业是 Technology
- **WHEN** 查 AAA 的板块
- **THEN** 板块是 XLK
- **AND** 数据源收到一次请求，没有写日志

#### Scenario: 数据源查不到这只股票

- **GIVEN** 数据源上没有 BBB
- **WHEN** 查 BBB 的板块
- **THEN** 没有板块，没有写日志

#### Scenario: 行业不在代码表里

- **GIVEN** 数据源上 CCC 的行业是 N/A
- **WHEN** 查 CCC 的板块
- **THEN** 没有板块
- **AND** 写了一条 WARNING 日志，内容里有 CCC 和 N/A

#### Scenario: 数据源不可用

- **GIVEN** 数据源对行业的请求报数据源不可用
- **WHEN** 查 AAA 的板块
- **THEN** 报数据源不可用，不返回没有板块

### Requirement: 财报的记录

财报日历的一条 SHALL 是一只股票的一次财报发布：

| 字段 | 说明 |
| --- | --- |
| 股票 | 股票代码 |
| 财年、季度 | 数据源给出的财年和季度（1 到 4） |
| 发布日 | 美东日期 |
| 发布时段 | 盘前、盘后、盘中；数据源没写时为空 |
| EPS 预期、EPS 实际 | 每股收益的一致预期和实际值；没有时为空 |
| 营收预期、营收实际 | 营收的一致预期和实际值，按数据源给的货币；没有时为空 |

同一只股票的同一个财年季度 SHALL 是同一次发布：发布日、发布时段、预期或实际值变化时，是这次发布的新版本，不是另一次发布。

一只股票的 surprise SHALL 是一组最近几个季度的记录，每个季度：

| 字段 | 说明 |
| --- | --- |
| 报告期 | 这个季度最后一天 |
| 财年、季度 | 数据源给出的财年和季度 |
| EPS 实际、EPS 预期 | 没有时为空 |
| surprise、surprise 百分比 | 数据源算好的实际减预期、以及它占预期的百分比；没有时为空 |

金额、每股收益和百分比 SHALL 用十进制精确保存数据源给出的值，不重新计算。

本 spec 下面三个要求的场景除非另有说明，都使用默认配置和下面的基准数据：

- 当前时刻 2026-10-14 15:00Z
- 数据源上 AAA（财年截至 5 月底）的财报日历：
  - 2027 财年第 1 季度：发布日 2026-09-24，盘后；EPS 预期 1.42、实际 1.47；营收预期 9,400,000,000、实际 9,535,000,000
  - 2027 财年第 2 季度：发布日 2026-12-17，没写发布时段；EPS 预期 1.7712、没有实际值；营收预期 9,900,000,000、没有实际值
- 数据源上 AAA 的 surprise：
  - 报告期 2026-08-31，2027 财年第 1 季度：EPS 实际 1.47、预期 1.42，surprise 0.05，surprise 百分比 3.5211
  - 报告期 2026-05-31，2026 财年第 4 季度：EPS 实际 1.30、预期 1.35，surprise -0.05，surprise 百分比 -3.7037
- 数据源上没有 BBB：财报日历和 surprise 都是空的

#### Scenario: 同一个财年季度是同一次发布

- **GIVEN** 已经刷新过 AAA
- **WHEN** 数据源上 AAA 2027 财年第 2 季度的发布日改为 2026-12-18、发布时段改为盘后，再刷新 AAA
- **THEN** 读到的 AAA 财报日历仍然只有两次发布，2027 财年第 2 季度的发布日是 2026-12-18，盘后
- **AND** 2027 财年第 2 季度有 2 个版本，版本 1 的发布日仍是 2026-12-17；2027 财年第 1 季度仍然只有 1 个版本

### Requirement: 刷新一只股票的财报

刷新一只股票的财报 SHALL：

1. 向数据源取这只股票发布日在窗口内（含两端）的财报日历。窗口从当前时刻的 UTC 日期往前「窗口往前天数」开始，到往后「窗口往后天数」为止
2. 向数据源取这只股票最近几个季度的 surprise，数据源给多少取多少
3. 两项都取到以后才保存：每次发布各保存一次，surprise 整组保存一次。内容和最新版本相同时不写入新版本（state-store spec「只追加的版本记录」）

任何一项取不到时 SHALL 报数据源不可用，MUST NOT 保存任何记录。这次结果里没有的发布（比如已经移出窗口）保持原来的版本，MUST NOT 删除。

#### Scenario: 第一次刷新

- **GIVEN** 基准数据，状态库里没有 AAA 的财报
- **WHEN** 刷新 AAA
- **THEN** 数据源依次收到两个请求：AAA 发布日在 2026-09-14 到 2027-01-12 之间的财报日历；AAA 的 surprise
- **AND** 读到 AAA 的财报日历是 2027 财年第 1 季度（2026-09-24）和第 2 季度（2026-12-17）两次发布，各有 1 个版本
- **AND** 读到 AAA 的 surprise 是报告期 2026-08-31 和 2026-05-31 两个季度

#### Scenario: 窗口按 UTC 日期

- **GIVEN** 基准数据，当前时刻 2026-10-14 02:00Z（美东还是 2026-10-13）
- **WHEN** 刷新 AAA
- **THEN** 财报日历的请求是发布日在 2026-09-14 到 2027-01-12 之间

#### Scenario: 发布以后有了实际值

- **GIVEN** 2026-10-14 15:00Z 刷新过 AAA
- **WHEN** 当前时刻 2026-12-18 15:00Z，数据源上 AAA 2027 财年第 2 季度有了实际值：EPS 1.85、营收 10,020,000,000；surprise 多了报告期 2026-11-30 的一个季度，排在最前面；再刷新 AAA
- **THEN** 财报日历的请求是发布日在 2026-11-18 到 2027-03-18 之间，结果里只有 2027 财年第 2 季度
- **AND** 读到 2027 财年第 2 季度的 EPS 实际 1.85、营收实际 10,020,000,000，有 2 个版本
- **AND** 2027 财年第 1 季度不在这次结果里，仍是原来的 1 个版本，仍然读得到
- **AND** 读到 AAA 的 surprise 是报告期 2026-11-30、2026-08-31、2026-05-31 三个季度

#### Scenario: 数据源上没有这只股票

- **GIVEN** 基准数据
- **WHEN** 刷新 BBB
- **THEN** 读到 BBB 的财报日历为空；读到 BBB 的 surprise 是空的一组，不是没有刷新过

#### Scenario: 取财报日历时数据源不可用

- **GIVEN** 基准数据，数据源对财报日历的请求报数据源不可用
- **WHEN** 刷新 AAA
- **THEN** 报数据源不可用
- **AND** 数据源没有收到 surprise 的请求；AAA 没有任何财报记录

#### Scenario: 取 surprise 时数据源不可用

- **GIVEN** 基准数据，数据源对 surprise 的请求报数据源不可用
- **WHEN** 刷新 AAA
- **THEN** 报数据源不可用
- **AND** 财报日历已经取到，也没有保存：AAA 没有任何财报记录

### Requirement: 读取财报

按股票读财报日历 SHALL 给出这只股票每次发布的最新版本，按发布日从早到晚排列；没有刷新过时为空。

按股票读 surprise SHALL 给出最近一次保存的那一组，按报告期从新到旧排列；没有刷新过时为空，和刷新过、数据源没有 surprise 的空组区分开。

读取只读状态库，MUST NOT 向数据源请求。

#### Scenario: 没有刷新过

- **GIVEN** 状态库里没有 CCC 的财报
- **WHEN** 读 CCC 的财报日历和 surprise
- **THEN** 财报日历为空；surprise 为空（没有刷新过）
- **AND** 数据源没有收到请求

#### Scenario: 数据源给的顺序不影响读到的顺序

- **GIVEN** 数据源给出的 AAA 财报日历先是 2027 财年第 2 季度、再是第 1 季度；surprise 先是报告期 2026-05-31、再是 2026-08-31
- **WHEN** 刷新 AAA，再读 AAA 的财报日历和 surprise
- **THEN** 财报日历依次是 2026-09-24、2026-12-17 两次发布
- **AND** surprise 依次是报告期 2026-08-31、2026-05-31

### Requirement: 财报的存储

财报日历和 surprise SHALL 存在状态库里（state-store spec），只由公司数据模块写入：财报日历按「股票 + 财年季度」保存每次发布的各个版本，surprise 按股票保存各个版本。

- 读回的记录 SHALL 和保存的完全相同：每一位小数、日期、空着的字段
- 用同一个数据库新建存储时（相当于进程重启），SHALL 读到之前保存的全部记录，之后的保存接着原来的版本号往后写
- 数据库连不上、表不存在、存着的记录读不出来时，读取和保存 SHALL 报错，MUST NOT 当作没有记录

#### Scenario: 原样读回

- **GIVEN** 基准数据
- **WHEN** 刷新 AAA，再读 AAA 的财报日历和 surprise
- **THEN** 2027 财年第 2 季度：发布日 2026-12-17，发布时段为空，EPS 预期正好是 1.7712，EPS 实际为空，营收预期正好是 9,900,000,000，营收实际为空
- **AND** 报告期 2026-05-31 的 surprise 正好是 -0.05，surprise 百分比正好是 -3.7037

#### Scenario: 重启后读到同样的记录

- **GIVEN** 已经刷新过 AAA
- **WHEN** 用同一个数据库新建存储，读 AAA 的财报日历和 surprise
- **THEN** 读到的和重启前相同
- **WHEN** 数据源上 AAA 2027 财年第 2 季度的发布日改为 2026-12-18，再刷新 AAA
- **THEN** 2027 财年第 2 季度有 2 个版本

#### Scenario: 数据库连不上

- **GIVEN** 基准数据，数据库连不上
- **WHEN** 刷新 AAA；读 AAA 的财报日历；读 AAA 的 surprise
- **THEN** 三个操作都报错，读取不返回空

### Requirement: 公司数据配置

公司数据模块的配置项 SHALL 可配置，默认值如下：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| 窗口往前天数 | 30 | 刷新财报日历时，取当前 UTC 日期往前这么多天以来的发布 |
| 窗口往后天数 | 90 | 刷新财报日历时，取到当前 UTC 日期往后这么多天为止的发布 |

两项都 SHALL 是大于 0 的整数，否则配置报错。

#### Scenario: 默认配置

- **WHEN** 不传任何配置项新建配置
- **THEN** 窗口往前 30 天，往后 90 天

#### Scenario: 改窗口

- **GIVEN** 基准数据，窗口往前 10 天、往后 60 天
- **WHEN** 刷新 AAA
- **THEN** 财报日历的请求是发布日在 2026-10-04 到 2026-12-13 之间

#### Scenario: 配置非法

- **WHEN** 窗口往前天数为 0，或者窗口往后天数为 -1
- **THEN** 配置报错
