# Spec Delta

## Purpose

moomoo 行情源通过 OpenD 读取市场快照，实现行情模块的行情源：把股票代码换成 OpenD 的代码，把快照字段映射成行情，并把调用失败统一报为行情不可用。

## ADDED Requirements

### Requirement: 请求

适配器 SHALL 在股票代码前加 `US.` 作为 OpenD 的代码（AAPL 是 US.AAPL，BRK.B 是 US.BRK.B），把一组股票放在一次快照请求里；返回的行情按原来的股票代码对应，和 OpenD 返回的顺序无关。

调用方给出的股票非空、不重复（行情模块保证，见 market-data spec「取行情」）。

本 spec 的场景除非另有说明，都使用下面的基准数据，取自 2026-10-06 从真实 OpenD 读到的快照（美东夏令时，当时是盘后）：

| OpenD 代码 | update_time | last_price | prev_close_price | volume | pre_price | after_price |
| --- | --- | --- | --- | --- | --- | --- |
| US.AAPL | 2026-10-06 17:53:59.407 | 333.63 | 332.89 | 30421856 | 332.01 | 333.85 |
| US.SPY | 2026-10-06 17:53:52.101 | 779.09 | 774.83 | 35734306 | 778.15 | 779.74 |
| US.BRK.B | 2026-10-06 17:53:22.816 | 505.54 | 504.26 | 3246092 | 504.39 | 505.56 |
| US.ZZZZQQ（不存在的代码） | 空 | 0 | 0 | 0 | N/A | N/A |

"OpenD 返回某行"指快照里有这一行；没写的字段取上表的值。

#### Scenario: 一次请求，按代码对应

- **GIVEN** OpenD 按 US.SPY、US.AAPL 的顺序返回两行
- **WHEN** 取 AAPL、SPY 的行情
- **THEN** OpenD 只收到一次快照请求，代码是 US.AAPL、US.SPY
- **AND** 结果里 AAPL 的最新价是 333.63，SPY 的最新价是 779.09

#### Scenario: 带点的股票代码

- **GIVEN** OpenD 返回 US.BRK.B
- **WHEN** 取 BRK.B 的行情
- **THEN** OpenD 收到的代码是 US.BRK.B
- **AND** 结果里有 BRK.B 一条，最新价 505.54

### Requirement: 快照的映射

适配器 SHALL 按下表把快照的一行映射为一只股票的行情：

| 行情 | 快照 |
| --- | --- |
| 最新价 | last_price；成交量（volume，只算常规交易时段）为 0 时没有最新价 |
| 前一交易日收盘价 | prev_close_price |
| 更新时刻 | update_time：美东时间，格式 `yyyy-MM-dd HH:mm:ss`，可能带毫秒；换成 UTC |

- 价格 SHALL 按十进制读取，取浮点数最短的十进制写法：快照里的 333.63 就是 333.63
- 美东时间按当天是夏令时还是冬令时换算，不依赖本机时区
- 盘前（pre_price）、盘后（after_price）、夜盘（overnight_price）的价格不用

#### Scenario: 夏令时的快照

- **GIVEN** OpenD 返回 US.AAPL
- **WHEN** 取 AAPL 的行情
- **THEN** 最新价 333.63，前一交易日收盘价 332.89，更新时刻 2026-10-06 21:53:59.407Z
- **AND** 盘后价 333.85 和盘前价 332.01 都没有用到

#### Scenario: 冬令时、不带毫秒的更新时刻

- **GIVEN** OpenD 返回 US.AAPL，update_time 是 2026-12-01 10:00:00
- **WHEN** 取 AAPL 的行情
- **THEN** 更新时刻 2026-12-01 15:00:00Z

#### Scenario: 开盘后还没有成交

- **GIVEN** OpenD 返回 US.AAPL，成交量 0，last_price 仍是 333.63，update_time 2026-10-07 09:29:58.120
- **WHEN** 取 AAPL 的行情
- **THEN** 没有最新价，前一交易日收盘价 332.89，更新时刻 2026-10-07 13:29:58.120Z

#### Scenario: 低价股的价格精确读取

- **GIVEN** OpenD 返回 US.AAPL，last_price 0.1234，prev_close_price 0.1199
- **WHEN** 取 AAPL 的行情
- **THEN** 最新价正好是 0.1234，前一交易日收盘价正好是 0.1199

### Requirement: 查不到的股票

OpenD 查不到一只股票（比如代码不存在）时，仍会在快照里返回这一行，update_time 为空、价格全为 0。适配器 SHALL 把 update_time 为空的行从结果里去掉，不让价格 0 进入止损和紧急退出的判断。

#### Scenario: 不存在的代码

- **GIVEN** OpenD 返回 US.AAPL 和 US.ZZZZQQ
- **WHEN** 取 AAPL、ZZZZQQ 的行情
- **THEN** 结果只有 AAPL 一条

### Requirement: 调用失败

OpenD 返回错误时，适配器 SHALL 报行情不可用，并带上 OpenD 给出的错误信息。这包括：在超时时长内没有连上 OpenD、连接中断、超过快照的频率限制（每 30 秒 60 次）、请求被 OpenD 拒绝。适配器 MUST NOT 自己重试。

#### Scenario: OpenD 返回错误

- **GIVEN** OpenD 对快照请求返回错误，错误信息是"请求频率太高"
- **WHEN** 取 AAPL 的行情
- **THEN** 报行情不可用，错误信息里有"请求频率太高"
- **AND** OpenD 只收到一次快照请求

### Requirement: 连接配置

连接 OpenD 的配置项 SHALL 可配置，默认值如下：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| 地址 | 127.0.0.1 | OpenD 所在的机器 |
| 端口 | 11111 | OpenD 的 API 端口 |
| 超时 | 10 秒 | 没有连上 OpenD 时，一次取行情最多等这么久 |

地址不能为空，端口在 1 到 65535 之间，超时大于 0，否则配置报错。

建立连接 SHALL NOT 等待 OpenD 连上：OpenD 没开时也立即返回，之后在后台不断重连。连上之前取行情，等到超时报行情不可用；连上以后取行情恢复正常。

#### Scenario: 默认连接配置

- **WHEN** 不传任何配置项新建连接配置
- **THEN** 地址 127.0.0.1，端口 11111，超时 10 秒

#### Scenario: 连接配置非法

- **WHEN** 地址为空字符串，或者端口为 0，或者超时为 0 秒
- **THEN** 配置报错

#### Scenario: OpenD 没开

- **GIVEN** 配置的端口上没有 OpenD，超时 1 秒
- **WHEN** 建立连接
- **THEN** 立即返回，不等 OpenD 连上
- **WHEN** 取 AAPL 的行情
- **THEN** 大约 1 秒后报行情不可用
