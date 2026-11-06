# Spec Delta

## MODIFIED Requirements

### Requirement: Cboe 数据源

适配器 SHALL 读配置的地址（默认 `https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`，不能为空），第一行是表头 DATE、OPEN、HIGH、LOW、CLOSE，日期写成 MM/DD/YYYY，把每一行映射为日期和收盘值，收盘值用 Decimal。

下面的情况 SHALL 报 VIX 不可用，不重试：超时、连不上、回复不是 200、表头不对、某一行的日期或收盘值读不出来、没有数据行。

#### Scenario: 读文件

- **GIVEN** 文件有两行：10/13/2026，收盘 19.200000；10/14/2026，收盘 21.000000
- **WHEN** 读 VIX 收盘值
- **THEN** 结果是 2026-10-13 的 19.2 和 2026-10-14 的 21，按文件的顺序

#### Scenario: 超时

- **GIVEN** 请求超时
- **WHEN** 读 VIX 收盘值
- **THEN** 报 VIX 不可用

#### Scenario: 回复不是 200

- **GIVEN** 回复 403
- **WHEN** 读 VIX 收盘值
- **THEN** 报 VIX 不可用

#### Scenario: 格式不对

- **GIVEN** 表头缺少 CLOSE；或者某一行的收盘值读不出来；或者文件只有表头
- **WHEN** 读 VIX 收盘值
- **THEN** 都报 VIX 不可用
