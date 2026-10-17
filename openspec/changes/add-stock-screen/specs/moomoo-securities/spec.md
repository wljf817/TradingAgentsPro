# Spec Delta

## Purpose

moomoo 股票列表通过 OpenD 实现寻股粗筛模块的股票列表数据源：用条件选股取出市值不低于下限的美股，用股票静态信息查交易所和证券类型，控制请求的间隔，并把调用失败报为数据源不可用。

## ADDED Requirements

### Requirement: 条件选股的请求和分页

取股票范围时，适配器 SHALL 向 OpenD 发条件选股请求：市场是美股，只有一个条件"市值不低于市值下限"，按市值从高到低排序，每页 200 只（OpenD 允许的上限），从第 0 只开始；回复不是最后一页时，从下一只接着请求，直到最后一页。

OpenD 的代码去掉 `US.` 前缀就是股票代码（US.BRK.B 是 BRK.B）。翻页之间排序变化导致同一只股票出现两次时，SHALL 只留第一次。

本 spec 的场景除非另有说明，都使用下面的基准数据：

- 市值下限 20 亿美元，请求间隔 3 秒
- 条件选股只有一页、是最后一页：US.AAPL、US.TSM、US.BRK.B、US.SPY、US.NSRGY、US.AMXQ、US.ZZZZQQ
- 静态信息：

  | OpenD 代码 | stock_type | exchange_type | delisting |
  | --- | --- | --- | --- |
  | US.AAPL | STOCK | US_NASDAQ | False |
  | US.TSM | STOCK | US_NYSE | False |
  | US.BRK.B | STOCK | US_NYSE | False |
  | US.SPY | ETF | US_AMEX | False |
  | US.NSRGY | STOCK | US_PINK | False |
  | US.AMXQ | STOCK | US_AMEX | False |
  | US.ZZZZQQ | N/A | N/A | True |

#### Scenario: 一页

- **GIVEN** 基准数据
- **WHEN** 取股票范围
- **THEN** OpenD 先收到一次条件选股请求：美股，市值不低于 2,000,000,000，按市值从高到低，从第 0 只开始取 200 只
- **AND** 结果是 AAPL、TSM、BRK.B、SPY、NSRGY、AMXQ 六只，没有 ZZZZQQ

#### Scenario: 三页

- **GIVEN** 条件选股第一页 200 只、第二页 200 只都不是最后一页，第三页 5 只是最后一页，静态信息里都是纳斯达克的 STOCK
- **WHEN** 取股票范围
- **THEN** 条件选股的三次请求依次从第 0、200、400 只开始，每次取 200 只
- **AND** 结果有 405 只

#### Scenario: 翻页之间重复的股票

- **GIVEN** 条件选股第一页有 US.AAPL、US.TSM，不是最后一页；第二页有 US.TSM、US.BRK.B，是最后一页
- **WHEN** 取股票范围
- **THEN** 结果依次是 AAPL、TSM、BRK.B，TSM 只出现一次

### Requirement: 静态信息的映射

条件选股取完以后，适配器 SHALL 把得到的代码按每次 200 个分批，向 OpenD 查股票静态信息，按下表把每一行映射为一只股票的上市信息（不含日均成交额和板块）：

| 上市信息 | 静态信息 |
| --- | --- |
| 股票代码 | code 去掉 `US.` |
| 交易所 | exchange_type：US_NYSE 是纽交所，US_NASDAQ 是纳斯达克，其余（包括 US_AMEX、US_PINK）是其他 |
| 证券类型 | stock_type：STOCK 是普通股，ETF 是 ETF，其余是其他 |

- moomoo 不区分 ADR：ADR 的 stock_type 也是 STOCK，记为普通股
- delisting 为 True 的行是 OpenD 认不出的股票（退市或不存在），SHALL 不在结果里
- 结果按条件选股给出的顺序排列

#### Scenario: 交易所和证券类型的映射

- **GIVEN** 基准数据
- **WHEN** 取股票范围
- **THEN** AAPL 是纳斯达克的普通股；TSM（ADR）是纽交所的普通股；BRK.B 是纽交所的普通股
- **AND** SPY 是其他交易所的 ETF；NSRGY 是其他交易所的普通股；AMXQ 是其他交易所的普通股

#### Scenario: 分批查静态信息

- **GIVEN** 条件选股取到 450 只，静态信息里都是纳斯达克的 STOCK
- **WHEN** 取股票范围
- **THEN** OpenD 依次收到三次静态信息请求，分别是第 1 到 200、201 到 400、401 到 450 只的代码

#### Scenario: OpenD 认不出的代码

- **GIVEN** 基准数据
- **WHEN** 取股票范围
- **THEN** 结果里没有 ZZZZQQ

### Requirement: 一只股票的上市信息

查一只股票时，适配器 SHALL 用这一个代码向 OpenD 查股票静态信息，按「静态信息的映射」给出它的交易所和证券类型。静态信息里没有这一行，或者 delisting 为 True 时，SHALL 给出查不到。

#### Scenario: 查到

- **GIVEN** 基准数据
- **WHEN** 查 BRK.B
- **THEN** OpenD 收到一次静态信息请求，代码只有 US.BRK.B
- **AND** BRK.B 是纽交所的普通股

#### Scenario: 查不到

- **GIVEN** 基准数据
- **WHEN** 查 ZZZZQQ；查 QQQQZZ（静态信息里没有这一行）
- **THEN** 都是查不到，不报错

### Requirement: 请求间隔

条件选股每 30 秒最多 10 次。适配器 SHALL 在每个对 OpenD 的请求（条件选股和静态信息，成功或失败）之后等待「请求间隔」，再发下一个请求或返回，这样相邻两个请求、包括相邻两次调用的请求，开始的时间至少隔开请求间隔。

#### Scenario: 每个请求之后等待

- **GIVEN** 基准数据
- **WHEN** 取股票范围
- **THEN** 依次是：条件选股请求，等待 3 秒，静态信息请求，等待 3 秒

#### Scenario: 失败的请求之后也等待

- **GIVEN** 基准数据，条件选股请求失败
- **WHEN** 取股票范围
- **THEN** 依次是：条件选股请求，等待 3 秒，然后报数据源不可用

### Requirement: 请求失败

OpenD 对任何一个请求返回失败（没有连上 OpenD、超时、限频、OpenD 报错）时，适配器 SHALL 报数据源不可用，带上 OpenD 给出的错误信息；已经取到的部分 MUST NOT 返回。适配器 MUST NOT 自己重试。

#### Scenario: 翻页途中失败

- **GIVEN** 条件选股第一页 200 只不是最后一页，第二页的请求失败
- **WHEN** 取股票范围
- **THEN** 报数据源不可用，不返回第一页的股票，也没有查静态信息

#### Scenario: 查静态信息失败

- **GIVEN** 基准数据，静态信息请求失败
- **WHEN** 取股票范围；查 AAPL
- **THEN** 都报数据源不可用

### Requirement: moomoo 股票列表配置

适配器 SHALL 可以配置：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| 请求间隔 | 3 秒 | 每个请求之后等这么久；条件选股每 30 秒最多 10 次 |

请求间隔 MUST 大于 0，否则配置报错。连接 OpenD 的地址和端口沿用 moomoo 行情的连接配置。

#### Scenario: 请求间隔非法

- **WHEN** 请求间隔为 0 秒
- **THEN** 配置报错
