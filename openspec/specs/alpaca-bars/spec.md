# alpaca-bars Specification

## Purpose

Alpaca 日线通过 Alpaca 市场数据 API 的历史 K 线和公司行动接口，给寻股粗筛提供按拆股调整的日线（分批取），给估值提供一只股票的原始收盘价和拆股记录，给行情模块提供几只股票的原始 15 分钟线、原始日线和拆股记录：都取完全部分页，日线映射为美东日期，调用失败分成数据源不可用和原样报错两类。

## Requirements

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

### Requirement: 原始收盘价

取一只股票从开始日期到结束日期（含两端）的原始收盘价时，适配器 SHALL 向 Alpaca 的 `/v2/stocks/bars` 发 GET 请求，参数和「请求和分页」相同，只有两项不同：symbols 只有这一只股票，adjustment 是 raw（不按拆股和分红调整）。请求 MUST NOT 带结束时刻，分页和认证同「请求和分页」。

每根日线 SHALL 映射为一个交易日的原始收盘价：交易日按「日线的映射」换成美东日期，收盘价是 c，按回复里写的十进制精确读取；交易日早于开始日期或晚于结束日期的去掉，按交易日从早到晚排列；回复里没有这只股票或者日线为空时，结果为空。

#### Scenario: 请求原始收盘价

- **GIVEN** Alpaca 回复 AAA 的两根日线：t 是 2026-10-13T04:00:00Z、c 是 128；t 是 2026-10-14T04:00:00Z、c 是 122.4；没有下一页
- **WHEN** 取 AAA 从 2026-10-13 到 2026-10-13 的原始收盘价
- **THEN** Alpaca 收到一个请求：symbols 是 AAA，timeframe 1Day，start 2026-10-13，adjustment raw，feed sip，limit 10000，sort asc，没有 end
- **AND** 结果只有 2026-10-13 的收盘价 128

#### Scenario: 原始收盘价分页

- **GIVEN** 第一页回复 AAA 2026-10-13 的日线，下一页标记 P2；带 P2 的请求回复 2026-10-14 的日线，没有下一页
- **WHEN** 取 AAA 从 2026-10-13 到 2026-10-14 的原始收盘价
- **THEN** Alpaca 依次收到两个请求，第二个带 page_token P2
- **AND** 结果是两天的收盘价

#### Scenario: 没有日线

- **GIVEN** Alpaca 回复里没有 AAA
- **WHEN** 取 AAA 的原始收盘价
- **THEN** 结果为空

### Requirement: 拆股记录

取一只股票除权日不晚于结束日期的拆股记录时，适配器 SHALL 向 Alpaca 的 `/v1/corporate-actions` 发 GET 请求，带上 API key ID 和 API secret，参数是：

| 参数 | 值 |
| --- | --- |
| symbols | 这只股票 |
| types | forward_split,reverse_split：正向和反向拆股 |
| start | 2000-01-01：早于 Alpaca 最早的拆股记录 |
| end | 结束日期，写成 `YYYY-MM-DD` |
| limit | 1000 |

回复里有下一页的标记（next_page_token）时，SHALL 带着它（page_token）和同样的参数再请求，直到没有下一页。

回复的 corporate_actions 里 forward_splits 和 reverse_splits 的每一条 SHALL 映射为一次拆股：除权日是 ex_date，拆股比例是 new_rate 除以 old_rate，按十进制精确计算。拆股按除权日从早到晚排列；除权日晚于结束日期的去掉；corporate_actions 是空对象时结果为空。

#### Scenario: 请求拆股记录

- **GIVEN** API key ID 是 K1，API secret 是 S1；Alpaca 回复 forward_splits 有 NVDA 的两条：ex_date 2021-07-20，old_rate 1，new_rate 4；ex_date 2024-06-10，old_rate 1，new_rate 10；没有下一页
- **WHEN** 取 NVDA 除权日不晚于 2026-10-14 的拆股记录
- **THEN** Alpaca 收到一个请求：symbols 是 NVDA，types 是 forward_split,reverse_split，start 2000-01-01，end 2026-10-14，limit 1000，带有 K1 和 S1
- **AND** 结果依次是 2021-07-20 比例 4、2024-06-10 比例 10

#### Scenario: 反向拆股

- **GIVEN** Alpaca 回复 reverse_splits 有 GE 的一条：ex_date 2021-08-02，old_rate 8，new_rate 1
- **WHEN** 取 GE 的拆股记录
- **THEN** 结果是 2021-08-02 比例 0.125

#### Scenario: 没有拆股

- **GIVEN** Alpaca 回复的 corporate_actions 是空对象
- **WHEN** 取 AAA 的拆股记录
- **THEN** 结果为空

#### Scenario: 拆股记录分页

- **GIVEN** 第一页回复 NVDA 2021-07-20 的一条，下一页标记 P2；带 P2 的请求回复 2024-06-10 的一条，没有下一页
- **WHEN** 取 NVDA 的拆股记录
- **THEN** Alpaca 依次收到两个请求，第二个带 page_token P2，其他参数和第一个相同
- **AND** 结果是两次拆股

### Requirement: 原始收盘价和拆股记录的失败

取原始收盘价和拆股记录失败时，适配器 SHALL 按「日线请求失败的分类」的表处理：超时、连不上、连接中断、429 和 5xx 报数据源不可用（公司数据模块的数据源不可用）；其他 4xx 和读不出来的回复原样报错。任何一页失败整次失败，MUST NOT 自己重试。

#### Scenario: 原始收盘价超时

- **WHEN** 取 AAA 的原始收盘价，Alpaca 超过超时时长没有响应
- **THEN** 报数据源不可用

#### Scenario: 拆股记录限频

- **WHEN** 取 AAA 的拆股记录，Alpaca 返回 429
- **THEN** 报数据源不可用，Alpaca 只收到一个请求

#### Scenario: 拆股记录服务端错误

- **WHEN** 取 AAA 的拆股记录，Alpaca 返回 503
- **THEN** 报数据源不可用

#### Scenario: 拆股记录的 key 不对

- **WHEN** 取 AAA 的拆股记录，Alpaca 返回 401
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 拆股记录缺字段

- **WHEN** 取 AAA 的拆股记录，回复的一条 forward_splits 缺了 new_rate
- **THEN** 原样报错，不报数据源不可用

### Requirement: 原始 K 线

给行情模块取一组股票的原始 15 分钟线（从开始时刻到结束时刻）或原始日线（从开始日期到结束时刻）时，适配器 SHALL 向 Alpaca 的 `/v2/stocks/bars` 发 GET 请求，参数是：

| 参数 | 15 分钟线 | 日线 |
| --- | --- | --- |
| symbols | 全部股票，用逗号连接，不分批 | 同左 |
| timeframe | 15Min | 1Day |
| start | 开始时刻，写成 UTC 的 `YYYY-MM-DDTHH:MM:SSZ` | 开始日期，写成 `YYYY-MM-DD` |
| end | 结束时刻，写成 UTC 的 `YYYY-MM-DDTHH:MM:SSZ` | 同左 |
| adjustment | raw：不按拆股和分红调整 | 同左 |
| feed | sip | 同左 |
| limit | 10000 | 同左 |
| sort | asc | 同左 |

结束时刻由行情模块保证早于现在至少 15 分钟（免费档取不了最近 15 分钟，Alpaca 返回 403）。分页和认证同「请求和分页」。Alpaca 按开始时刻不晚于结束时刻返回整根 K 线，适配器不再按时刻过滤。

每根 K 线 SHALL 映射为：15 分钟线的开始时刻是 t；日线的交易日按「日线的映射」把 t 换成美东日期；开盘价 o、最高价 h、最低价 l、收盘价 c 按回复里写的十进制精确读取，成交量 v。每只股票按时间从早到晚排列；回复里没有、或者 K 线为空的股票不在结果里。

#### Scenario: 15 分钟线的请求

- **GIVEN** Alpaca 回复 AAA 的一根 15 分钟线：t 是 2026-11-25T14:30:00Z，o 10，h 11.25，l 9.5，c 10.875，v 1200；没有下一页
- **WHEN** 取 AAA、SPY 从 2026-11-25 14:30Z 到 2026-11-25 16:00Z 的原始 15 分钟线
- **THEN** Alpaca 收到一个请求：symbols 是 AAA,SPY，timeframe 15Min，start 2026-11-25T14:30:00Z，end 2026-11-25T16:00:00Z，adjustment raw，feed sip，limit 10000，sort asc
- **AND** 结果只有 AAA 的一根：开始时刻 2026-11-25 14:30Z，开 10，高 11.25，低 9.5，收正好是 10.875，量 1,200

#### Scenario: 日线的请求

- **GIVEN** Alpaca 回复 AAA 的一根日线：t 是 2026-11-25T05:00:00Z，o 10，h 12，l 9，c 11，v 5000
- **WHEN** 取 AAA 从 2026-11-23 到 2026-11-25 21:15Z 的原始日线
- **THEN** Alpaca 收到一个请求：symbols 是 AAA，timeframe 1Day，start 2026-11-23，end 2026-11-25T21:15:00Z，adjustment raw，feed sip，limit 10000，sort asc
- **AND** 结果是 AAA 2026-11-25 一根：开 10，高 12，低 9，收 11，量 5,000

#### Scenario: 原始 K 线分页

- **GIVEN** 第一页回复 AAA 14:30Z 的 15 分钟线，下一页标记 P2；带 P2 的请求回复 14:45Z 的，没有下一页
- **WHEN** 取 AAA 的原始 15 分钟线
- **THEN** Alpaca 依次收到两个请求，第二个带 page_token P2，其他参数和第一个相同
- **AND** 结果是 14:30Z、14:45Z 两根

### Requirement: 行情模块的拆股记录

给行情模块取一只股票除权日不晚于结束日期的拆股记录时，适配器 SHALL 按「拆股记录」的请求、分页和映射处理，失败按「原始 K 线的失败」。

#### Scenario: 行情模块取拆股记录

- **GIVEN** Alpaca 回复 forward_splits 有 NVDA 的一条：ex_date 2024-06-10，old_rate 1，new_rate 10
- **WHEN** 行情模块取 NVDA 除权日不晚于 2026-11-25 的拆股记录
- **THEN** Alpaca 收到一个请求：symbols 是 NVDA，types 是 forward_split,reverse_split，start 2000-01-01，end 2026-11-25，limit 1000
- **AND** 结果是 2024-06-10 比例 10

### Requirement: 原始 K 线的失败

取原始 K 线和行情模块的拆股记录失败时，适配器 SHALL 按「日线请求失败的分类」的表处理：超时、连不上、连接中断、429 和 5xx 报行情模块的 K 线不可用；其他 4xx（包括取最近 15 分钟的 403）和读不出来的回复原样报错。任何一页失败整次失败，MUST NOT 自己重试。

#### Scenario: 原始 K 线超时

- **WHEN** 取 AAA 的原始 15 分钟线，Alpaca 超过超时时长没有响应
- **THEN** 报 K 线不可用

#### Scenario: 原始 K 线限频

- **WHEN** 取 AAA 的原始日线，Alpaca 返回 429
- **THEN** 报 K 线不可用，Alpaca 只收到一个请求

#### Scenario: 原始 K 线服务端错误

- **WHEN** 取 AAA 的原始日线，Alpaca 返回 503
- **THEN** 报 K 线不可用

#### Scenario: 取最近 15 分钟

- **WHEN** 取 AAA 的原始 15 分钟线，Alpaca 返回 403
- **THEN** 原样报错，不报 K 线不可用

#### Scenario: 原始 K 线缺字段

- **WHEN** 取 AAA 的原始 15 分钟线，回复的一根缺了 h
- **THEN** 原样报错，不报 K 线不可用

#### Scenario: 行情模块的拆股记录不可用

- **WHEN** 行情模块取 AAA 的拆股记录，Alpaca 返回 503
- **THEN** 报 K 线不可用
