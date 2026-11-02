# Spec Delta

## MODIFIED Requirements

### Requirement: 快照的映射

适配器 SHALL 按下表把快照的一行映射为一只股票的行情：

| 行情 | 快照 |
| --- | --- |
| 最新价 | 当天常规时段的快照是 last_price，其余时候没有（见下） |
| 前一交易日收盘价 | 还没切换到当天的快照是 last_price，其余时候是 prev_close_price |
| 量比 | volume_ratio；没有最新价时也没有量比 |
| 成交量 | volume，取整数股；没有最新价时也没有成交量 |
| 更新时刻 | update_time：美东时间，格式 `yyyy-MM-dd HH:mm:ss`，可能带毫秒；换成 UTC |

OpenD 在开盘前切换到当天：prev_close_price 换成上一个交易日的收盘价，open_price 和成交量清零；切换以前，常规时段的字段都是上一个交易日的，last_price 就是上一个交易日的收盘价。适配器 SHALL 按 open_price 和 update_time 判断快照处在哪一段，开盘时刻是 update_time 那天美东 09:30：

| open_price | update_time | 快照 | 最新价 | 前一交易日收盘价 |
| --- | --- | --- | --- | --- |
| 不为 0 | 早于开盘时刻 | 还没切换到当天 | 没有 | last_price |
| 0 | 任何时刻 | 已经切换，当天还没有开盘成交 | 没有 | prev_close_price |
| 不为 0 | 开盘时刻到开盘后 1 分钟以前 | 刚开盘，last_price 可能还是上一个交易日的收盘价 | 没有 | prev_close_price |
| 不为 0 | 开盘后 1 分钟及以后 | 当天常规时段 | last_price | prev_close_price |

- 成交量不用来判断快照处在哪一段：切换以后、开盘以前成交量就可能不为 0
- 价格和量比 SHALL 按十进制读取，取浮点数最短的十进制写法：快照里的 333.63 就是 333.63
- 美东时间按当天是夏令时还是冬令时换算，不依赖本机时区
- 盘前（pre_price）、盘后（after_price）、夜盘（overnight_price）的价格不用

"OpenD 返回 US.AAPL"没写 volume_ratio 时，volume_ratio 是 1.37；volume 是 30421856.0（SDK 给的是浮点数）。

#### Scenario: 夏令时的快照

- **GIVEN** OpenD 返回 US.AAPL
- **WHEN** 取 AAPL 的行情
- **THEN** 最新价 333.63，前一交易日收盘价 332.89，量比 1.37，成交量 30,421,856，更新时刻 2026-10-06 21:53:59.407Z
- **AND** 盘后价 333.85 和盘前价 332.01 都没有用到

#### Scenario: 冬令时、不带毫秒的更新时刻

- **GIVEN** OpenD 返回 US.AAPL，update_time 是 2026-12-01 10:00:00
- **WHEN** 取 AAPL 的行情
- **THEN** 更新时刻 2026-12-01 15:00:00Z

#### Scenario: 还没切换到当天

- **GIVEN** OpenD 返回 US.AAPL，update_time 2026-10-07 09:11:00（其余字段还是 10-06 的）
- **WHEN** 取 AAPL 的行情
- **THEN** 没有最新价，没有量比，没有成交量，前一交易日收盘价 333.63，更新时刻 2026-10-07 13:11:00Z

"切换以后的 US.AAPL"指 OpenD 返回 US.AAPL，open_price 0，prev_close_price 333.63，last_price 333.63，成交量 695314，volume_ratio 0.8，update_time 2026-10-07 09:29:48.302。

#### Scenario: 切换以后还没有开盘成交

- **GIVEN** OpenD 返回切换以后的 US.AAPL
- **WHEN** 取 AAPL 的行情
- **THEN** 没有最新价，没有量比，没有成交量（快照的成交量 695314 不用），前一交易日收盘价 333.63
- **WHEN** 改为 update_time 2026-10-07 09:35:00，再取 AAPL 的行情
- **THEN** 仍然没有最新价，没有量比

#### Scenario: 开盘后的头 1 分钟

- **GIVEN** OpenD 返回切换以后的 US.AAPL，open_price 335.1，update_time 2026-10-07 09:30:59.999
- **WHEN** 取 AAPL 的行情
- **THEN** 没有最新价，没有量比，前一交易日收盘价 333.63

#### Scenario: 开盘 1 分钟以后

- **GIVEN** OpenD 返回切换以后的 US.AAPL，open_price 335.1，last_price 336.2，volume_ratio 2.1，volume 812345.0，update_time 2026-10-07 09:31:00
- **WHEN** 取 AAPL 的行情
- **THEN** 最新价 336.2，前一交易日收盘价 333.63，量比 2.1，成交量 812,345，更新时刻 2026-10-07 13:31:00Z

#### Scenario: 低价股的价格精确读取

- **GIVEN** OpenD 返回 US.AAPL，last_price 0.1234，prev_close_price 0.1199
- **WHEN** 取 AAPL 的行情
- **THEN** 最新价正好是 0.1234，前一交易日收盘价正好是 0.1199

#### Scenario: 量比精确读取

- **GIVEN** OpenD 返回 US.AAPL，volume_ratio 3.05
- **WHEN** 取 AAPL 的行情
- **THEN** 量比正好是 3.05
