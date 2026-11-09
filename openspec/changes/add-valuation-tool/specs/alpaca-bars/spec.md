## ADDED Requirements

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
