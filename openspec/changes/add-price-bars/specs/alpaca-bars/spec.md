# Spec Delta

## ADDED Requirements

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
