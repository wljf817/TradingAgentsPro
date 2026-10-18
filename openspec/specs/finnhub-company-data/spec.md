# finnhub-company-data Specification

## Purpose

Finnhub 数据源用 Finnhub 免费档的 REST API 实现公司数据模块的数据源：取一只股票的行业、财报日历和最近几个季度的 EPS surprise，把返回的字段映射成公司数据模块的记录，并把调用失败分为数据源不可用和需要人处理的错误。

## Requirements

### Requirement: 请求

适配器 SHALL 向配置的网址发 GET 请求，API key 放在请求头 `X-Finnhub-Token` 里，MUST NOT 放进网址。股票代码原样作为 `symbol` 参数（BRK.B 就是 BRK.B）。

| 用途 | 接口 | 参数 |
| --- | --- | --- |
| 行业 | `/stock/profile2` | symbol |
| 财报日历 | `/calendar/earnings` | symbol；from、to：窗口的第一天和最后一天，`yyyy-MM-dd` |
| surprise | `/stock/earnings` | symbol |

每次调用 SHALL 只发一个请求，MUST NOT 重试，也 MUST NOT 为了限频而等待。

本 spec 的场景除非另有说明，都使用默认配置、API key `test-key` 和下面的基准数据。格式照 Finnhub 文档里各接口的示例（2026-10-17 读的 swagger），数值是编的：

- `/stock/profile2?symbol=AAPL` 返回：`{"country": "US", "currency": "USD", "exchange": "NASDAQ NMS - GLOBAL MARKET", "finnhubIndustry": "Technology", "name": "Apple Inc", "ticker": "AAPL", "marketCapitalization": 3950000}`
- `/calendar/earnings?symbol=AAPL&from=2026-09-14&to=2027-01-12` 返回：`{"earningsCalendar": [{"date": "2026-10-29", "epsActual": null, "epsEstimate": 1.7712, "hour": "amc", "quarter": 4, "revenueActual": null, "revenueEstimate": 101893000000, "symbol": "AAPL", "year": 2026}]}`
- `/stock/earnings?symbol=AAPL` 返回：`[{"actual": 1.57, "estimate": 1.4318, "period": "2026-06-30", "quarter": 3, "surprise": 0.1382, "surprisePercent": 9.6522, "symbol": "AAPL", "year": 2026}, {"actual": 1.65, "estimate": 1.6249, "period": "2026-03-31", "quarter": 2, "surprise": 0.0251, "surprisePercent": 1.5447, "symbol": "AAPL", "year": 2026}]`

#### Scenario: 行业的请求

- **WHEN** 取 AAPL 的行业
- **THEN** Finnhub 只收到一个请求：GET `/stock/profile2`，参数 symbol=AAPL
- **AND** 请求头 `X-Finnhub-Token` 是 test-key，网址里没有 test-key

#### Scenario: 财报日历的请求

- **WHEN** 取 AAPL 在 2026-09-14 到 2027-01-12 之间的财报日历
- **THEN** Finnhub 只收到一个请求：GET `/calendar/earnings`，参数 symbol=AAPL、from=2026-09-14、to=2027-01-12，请求头带着 key

#### Scenario: surprise 的请求

- **WHEN** 取 AAPL 的 surprise
- **THEN** Finnhub 只收到一个请求：GET `/stock/earnings`，参数 symbol=AAPL，请求头带着 key

#### Scenario: 带点的股票代码

- **WHEN** 取 BRK.B 的行业
- **THEN** 请求的参数是 symbol=BRK.B

### Requirement: 行业

适配器 SHALL 把 `/stock/profile2` 返回的 `finnhubIndustry` 原样作为行业，不去空白、不改大小写（对应板块时由公司数据模块处理，见 company-data spec「板块」）。下面这些情况没有行业：

- 返回空对象 `{}`：Finnhub 查不到这只股票
- 没有 `finnhubIndustry`，或者它是空字符串

#### Scenario: 有行业

- **WHEN** 取 AAPL 的行业
- **THEN** 行业是 Technology

#### Scenario: 行业原样给出

- **GIVEN** `/stock/profile2` 返回的 `finnhubIndustry` 是 "Energy "（末尾有空格）
- **WHEN** 取 AAPL 的行业
- **THEN** 行业是 "Energy "，末尾的空格还在

#### Scenario: 查不到这只股票

- **GIVEN** `/stock/profile2?symbol=ZZZZQQ` 返回 `{}`
- **WHEN** 取 ZZZZQQ 的行业
- **THEN** 没有行业

#### Scenario: 行业是空字符串

- **GIVEN** `/stock/profile2` 返回的 `finnhubIndustry` 是空字符串
- **WHEN** 取 AAPL 的行业
- **THEN** 没有行业

### Requirement: 财报日历的映射

适配器 SHALL 把 `/calendar/earnings` 返回的 `earningsCalendar` 里的每一项按下表映射为一次财报发布（company-data spec「财报的记录」），顺序和返回的相同；`earningsCalendar` 是空数组时没有发布。

| 财报发布 | Finnhub 的字段 |
| --- | --- |
| 股票 | symbol |
| 财年、季度 | year、quarter |
| 发布日 | date，`yyyy-MM-dd` |
| 发布时段 | hour，见下表 |
| EPS 预期、EPS 实际 | epsEstimate、epsActual；null 时为空 |
| 营收预期、营收实际 | revenueEstimate、revenueActual；null 时为空 |

| hour | 发布时段 |
| --- | --- |
| bmo | 盘前 |
| amc | 盘后 |
| dmh | 盘中 |
| 空字符串或 null | 空 |

数值 SHALL 按 JSON 里写的十进制精确读取，不经过浮点数：1.7712 就是 1.7712。

#### Scenario: 映射一次发布

- **WHEN** 取 AAPL 在 2026-09-14 到 2027-01-12 之间的财报日历
- **THEN** 结果是一次发布：AAPL，2026 财年第 4 季度，发布日 2026-10-29，盘后，EPS 预期 1.7712、EPS 实际为空，营收预期 101,893,000,000、营收实际为空

#### Scenario: 盘前发布

- **GIVEN** 基准数据里那一项的 hour 是 bmo
- **WHEN** 取 AAPL 的财报日历
- **THEN** 发布时段是盘前

#### Scenario: 盘后发布

- **WHEN** 取 AAPL 的财报日历（hour 是 amc）
- **THEN** 发布时段是盘后

#### Scenario: 盘中发布

- **GIVEN** 基准数据里那一项的 hour 是 dmh
- **WHEN** 取 AAPL 的财报日历
- **THEN** 发布时段是盘中

#### Scenario: 没写发布时段

- **GIVEN** 基准数据里那一项的 hour 分别是空字符串、null
- **WHEN** 取 AAPL 的财报日历
- **THEN** 两次的发布时段都为空

#### Scenario: 已经发布

- **GIVEN** 基准数据里那一项的 epsActual 是 1.85，revenueActual 是 103100000000
- **WHEN** 取 AAPL 的财报日历
- **THEN** EPS 实际正好是 1.85，营收实际正好是 103,100,000,000

#### Scenario: 窗口内没有发布

- **GIVEN** `/calendar/earnings` 返回 `{"earningsCalendar": []}`
- **WHEN** 取 ZZZZQQ 的财报日历
- **THEN** 没有发布

### Requirement: surprise 的映射

适配器 SHALL 把 `/stock/earnings` 返回的数组里的每一项按下表映射为一个季度的 surprise（company-data spec「财报的记录」），顺序和返回的相同；空数组时没有 surprise。数值同样按 JSON 里写的十进制精确读取。

| surprise | Finnhub 的字段 |
| --- | --- |
| 报告期 | period，`yyyy-MM-dd` |
| 财年、季度 | year、quarter |
| EPS 实际、EPS 预期 | actual、estimate；null 时为空 |
| surprise、surprise 百分比 | surprise、surprisePercent；null 时为空 |

#### Scenario: 映射 surprise

- **WHEN** 取 AAPL 的 surprise
- **THEN** 结果依次是：报告期 2026-06-30，2026 财年第 3 季度，EPS 实际 1.57、预期 1.4318，surprise 0.1382，surprise 百分比 9.6522；报告期 2026-03-31，2026 财年第 2 季度，EPS 实际 1.65、预期 1.6249，surprise 0.0251，surprise 百分比 1.5447

#### Scenario: 没有预期

- **GIVEN** 基准数据里第一项的 estimate、surprise、surprisePercent 都是 null
- **WHEN** 取 AAPL 的 surprise
- **THEN** 报告期 2026-06-30 的 EPS 实际 1.57，EPS 预期、surprise、surprise 百分比都为空

#### Scenario: 没有 surprise

- **GIVEN** `/stock/earnings?symbol=ZZZZQQ` 返回 `[]`
- **WHEN** 取 ZZZZQQ 的 surprise
- **THEN** 没有 surprise

### Requirement: 调用失败的分类

适配器 SHALL 按下表处理调用失败，错误信息里写明接口和原因，MUST NOT 含有 API key：

| 情况 | 结果 |
| --- | --- |
| 在超时时长内没有响应、连不上 | 报数据源不可用 |
| 429：超过每分钟的限额或每秒 30 次 | 报数据源不可用 |
| 5xx | 报数据源不可用 |
| 401、403：key 不对，或者接口不在免费档里 | 原样报 HTTP 错误，不算数据源不可用 |
| 其他 4xx | 原样报 HTTP 错误，不算数据源不可用 |
| 200，但返回的内容和上面的格式对不上（比如一项缺了 date） | 原样报格式错误，不算数据源不可用 |

数据源不可用是暂时的，调用方以后再取；另外三种要人处理配置或代码，MUST NOT 报成数据源不可用。

#### Scenario: 超时

- **GIVEN** Finnhub 在超时时长内没有响应
- **WHEN** 取 AAPL 的财报日历
- **THEN** 报数据源不可用，错误信息里有 `/calendar/earnings`
- **AND** Finnhub 只收到一个请求

#### Scenario: 连不上

- **GIVEN** 连不上 Finnhub
- **WHEN** 取 AAPL 的行业
- **THEN** 报数据源不可用

#### Scenario: 超过限额

- **GIVEN** Finnhub 对请求返回 429
- **WHEN** 取 AAPL 的 surprise
- **THEN** 报数据源不可用，错误信息里有 429
- **AND** Finnhub 只收到一个请求

#### Scenario: 服务端出错

- **GIVEN** Finnhub 对请求返回 503
- **WHEN** 取 AAPL 的财报日历
- **THEN** 报数据源不可用

#### Scenario: key 不对或者接口要付费

- **GIVEN** Finnhub 对请求分别返回 401 和 403
- **WHEN** 取 AAPL 的行业
- **THEN** 两次都原样报 HTTP 错误，不是数据源不可用
- **AND** 错误信息里没有 test-key

#### Scenario: 其他 4xx

- **GIVEN** Finnhub 对请求返回 400
- **WHEN** 取 AAPL 的财报日历
- **THEN** 原样报 HTTP 错误，不是数据源不可用

#### Scenario: 返回的格式对不上

- **GIVEN** `/calendar/earnings` 返回 200，`earningsCalendar` 里的一项没有 date
- **WHEN** 取 AAPL 的财报日历
- **THEN** 报格式错误，不是数据源不可用

### Requirement: Finnhub 配置

Finnhub 的配置项 SHALL 如下：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| API key | 没有，必须给出 | Finnhub 的 API key，由环境变量提供 |
| 网址 | `https://finnhub.io/api/v1` | Finnhub REST API 的地址 |
| 超时 | 10 秒 | 一个请求等多久没有响应就算超时 |

API key 和网址不能为空，超时大于 0，否则配置报错。显示配置时 MUST NOT 显示 API key 的值。

#### Scenario: 默认配置

- **WHEN** 只给出 API key 新建配置
- **THEN** 网址 `https://finnhub.io/api/v1`，超时 10 秒
- **AND** 配置的文字形式里没有 key 的值

#### Scenario: 配置非法

- **WHEN** 不给 API key，或者 API key 为空字符串，或者网址为空字符串，或者超时为 0 秒
- **THEN** 配置报错
