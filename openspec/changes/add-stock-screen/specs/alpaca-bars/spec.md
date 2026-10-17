# Spec Delta

## Purpose

Alpaca 日线通过 Alpaca 市场数据 API 的历史 K 线接口实现寻股粗筛模块的日线数据源：分批、取完全部分页，把按拆股调整的日线映射为美东日期的日线，并把调用失败分成数据源不可用和原样报错两类。

## ADDED Requirements

### Requirement: 请求和分页

取一组股票从开始日期到结束日期（含两端）的日线时，适配器 SHALL 把股票按给出的顺序每 100 只分一批，每批向 Alpaca 的 `/v2/stocks/bars` 发 GET 请求，参数是：

| 参数 | 值 |
| --- | --- |
| symbols | 这一批的股票，用逗号连接 |
| timeframe | 1Day |
| start | 开始日期，写成 `YYYY-MM-DD` |
| adjustment | split：价格和成交量按拆股调整，不按分红调整 |
| feed | sip：全市场的成交 |
| limit | 10000：每页最多 10,000 根，是 Alpaca 允许的上限，按全部股票合计 |
| sort | asc |

请求 MUST NOT 带结束时刻：免费档不能取最近 15 分钟的数据，Alpaca 不带结束时刻时默认取到它允许的最新时刻；结束日期以后的日线由「日线的映射」去掉。

每个请求 SHALL 带上 API key ID 和 API secret。回复里有下一页的标记（next_page_token）时，SHALL 带着它（page_token）和同样的参数再请求这一批，直到没有下一页；全部批、全部页的日线按股票合在一起返回。

本 spec 的场景除非另有说明，都使用下面的基准数据（Alpaca 回复里的日线）：

| 股票 | t | c | v | vw | o、h、l、n |
| --- | --- | --- | --- | --- | --- |
| AAA | 2026-10-13T04:00:00Z | 128 | 1000000 | 127.9512 | 有 |
| AAA | 2026-10-14T04:00:00Z | 122.4 | 1000000 | 123.0175 | 有 |
| SPY | 2026-10-13T04:00:00Z | 506 | 50000000 | 505.81 | 有 |
| SPY | 2026-10-14T04:00:00Z | 510 | 50000000 | 509.66 | 有 |

#### Scenario: 一页

- **GIVEN** Alpaca 回复基准数据，没有下一页
- **WHEN** 取 AAA、SPY 从 2026-10-13 到 2026-10-14 的日线
- **THEN** Alpaca 收到一个请求：symbols 是 AAA,SPY，timeframe 1Day，start 2026-10-13，adjustment split，feed sip，limit 10000，sort asc，没有 end
- **AND** 结果是 AAA 和 SPY 各两根日线

#### Scenario: 分页

- **GIVEN** 第一页回复 AAA 的两根日线，下一页标记 P2；带 P2 的请求回复 SPY 的两根日线，没有下一页
- **WHEN** 取 AAA、SPY 从 2026-10-13 到 2026-10-14 的日线
- **THEN** Alpaca 依次收到两个请求，第二个带 page_token P2，其他参数和第一个相同
- **AND** 结果是 AAA 和 SPY 各两根日线

#### Scenario: 分批

- **GIVEN** 150 只股票，Alpaca 对每个请求都回复空，没有下一页
- **WHEN** 取这 150 只股票的日线
- **THEN** Alpaca 依次收到两个请求，symbols 分别是第 1 到 100 只和第 101 到 150 只

#### Scenario: 请求带上认证

- **GIVEN** API key ID 是 K1，API secret 是 S1
- **WHEN** 取 AAA 的日线
- **THEN** Alpaca 收到的每个请求都带有 API key ID K1 和 API secret S1

### Requirement: 日线的映射

适配器 SHALL 按下表把 Alpaca 的一根日线映射为一个交易日的日线：

| 日线 | Alpaca |
| --- | --- |
| 交易日 | t 换成美东时间的日期（Alpaca 的日线时间戳是美东当天零点），按当天是夏令时还是冬令时换算，不依赖本机时区 |
| 收盘价 | c |
| 成交均价 | vw |
| 成交量 | v |

- 价格 SHALL 按回复里写的十进制精确读取
- o、h、l、n 不用
- 交易日早于开始日期或晚于结束日期的日线 SHALL 去掉
- 每只股票的日线按交易日从早到晚排列；回复里没有、或者日线为空的股票不在结果里

#### Scenario: 夏令时的时间戳

- **GIVEN** Alpaca 回复基准数据
- **WHEN** 取 AAA 从 2026-10-13 到 2026-10-14 的日线
- **THEN** AAA 依次是 2026-10-13：收盘价 128，成交均价正好是 127.9512，成交量 1,000,000；2026-10-14：收盘价 122.4，成交均价正好是 123.0175

#### Scenario: 冬令时的时间戳

- **GIVEN** Alpaca 回复 AAA 的一根日线，t 是 2026-12-01T05:00:00Z
- **WHEN** 取 AAA 从 2026-12-01 到 2026-12-01 的日线
- **THEN** AAA 有一根 2026-12-01 的日线

#### Scenario: 结束日期以后的日线去掉

- **GIVEN** Alpaca 回复基准数据
- **WHEN** 取 AAA、SPY 从 2026-10-13 到 2026-10-13 的日线
- **THEN** AAA、SPY 各只有 2026-10-13 一根，2026-10-14 的去掉了

#### Scenario: 没有日线的股票

- **GIVEN** Alpaca 回复里只有 AAA 的日线，没有 ZZZ；BBB 的日线是空数组
- **WHEN** 取 AAA、BBB、ZZZ 的日线
- **THEN** 结果里只有 AAA

### Requirement: 日线请求失败的分类

适配器 SHALL 按下表处理调用 Alpaca 的失败：

| 情况 | 结果 |
| --- | --- |
| 超时、连不上、连接中断；Alpaca 返回 429（限频）或 5xx | 数据源不可用，带上请求和状态码或错误 |
| Alpaca 返回其他 4xx（比如 401、403、400） | 原样报错：是 key、权限、订阅或程序的问题，不当作数据源不可用 |
| 回复的内容不是约定的格式 | 原样报错 |

任何一批、任何一页失败，整次调用按这一页的失败处理，前面取到的日线不返回。适配器 MUST NOT 自己重试。

#### Scenario: 超时

- **WHEN** 取 AAA 的日线，Alpaca 超过超时时长没有响应
- **THEN** 报数据源不可用

#### Scenario: 连不上

- **WHEN** 取 AAA 的日线，连不上 Alpaca
- **THEN** 报数据源不可用

#### Scenario: 限频

- **WHEN** 取 AAA 的日线，Alpaca 返回 429
- **THEN** 报数据源不可用，Alpaca 只收到一个请求

#### Scenario: 服务端错误

- **WHEN** 取 AAA 的日线，Alpaca 返回 503
- **THEN** 报数据源不可用

#### Scenario: key 不对

- **WHEN** 取 AAA 的日线，Alpaca 返回 401
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 订阅不允许

- **WHEN** 取 AAA 的日线，Alpaca 返回 403
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 回复缺字段

- **WHEN** 取 AAA 的日线，Alpaca 回复的日线缺了 vw
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 第二批失败

- **GIVEN** 150 只股票，第一批的请求回复成功，第二批的请求返回 503
- **WHEN** 取这 150 只股票的日线
- **THEN** 报数据源不可用，不返回第一批的日线

### Requirement: Alpaca 日线配置

适配器 SHALL 可以配置：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| 地址 | Alpaca 市场数据 API 的地址 | `https://data.alpaca.markets` |
| 超时时长 | 每次请求等多久没有响应就算超时 | 10 秒 |
| API key ID、API secret | 每次请求都带上，用于认证；和模拟盘用同一组 | 无，必须给出 |

超时时长 MUST 大于 0，否则配置报错。

#### Scenario: 超时时长非法

- **WHEN** 超时时长为 0 秒
- **THEN** 配置报错
