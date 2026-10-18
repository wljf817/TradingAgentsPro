# Spec Delta

## Purpose

SEC EDGAR 公告源通过 SEC 的公开数据（股票代码表和每家公司的 submissions）实现新闻和公告模块的公告源：把股票代码换成 CIK，取每家公司起始时刻以来受理的公告，换算受理时刻，并遵守 SEC 对 User-Agent 和请求频率的要求。

## ADDED Requirements

### Requirement: 股票代码到 CIK

适配器 SHALL 用 SEC 的股票代码表（`https://www.sec.gov/files/company_tickers.json`，每项有 cik_str 和 ticker）查每只股票所属公司的 CIK：

- 股票代码里的点换成横线再查：BRK.B 查 BRK-B
- 代码表在第一次需要时下载，之后一直用这一份
- 有股票在以前下载的代码表里查不到时，SHALL 重新下载一次代码表再查
- 刚下载的代码表里仍然查不到的股票没有公告，其他股票照常取，MUST NOT 报错；这只股票在本进程里不再触发重新下载
- 几只股票属于同一家公司（同一个 CIK）时，这家公司的公告列表只读一次，每份公告标注这几只股票

本 spec 的场景除非另有说明，都使用下面的基准数据，取自 2026-10-17 从 SEC 读到的数据。代码表里有：

| cik_str | ticker |
| --- | --- |
| 320193 | AAPL |
| 1067983 | BRK-B |
| 1652044 | GOOGL |
| 1652044 | GOOG |

AAPL（CIK 320193）的 submissions 里 filings.recent 的几行：

| accessionNumber | form | acceptanceDateTime | items | primaryDocument | 公告页面上写的受理时刻（美东） |
| --- | --- | --- | --- | --- | --- |
| 0001140361-26-039785 | 4 | 2026-10-16T02:30:17.000Z | 空 | xslF345X06/form4.xml | 2026-10-15 18:30:17 |
| 0000320193-26-000020 | 10-Q | 2026-07-31T14:01:02.000Z | 空 | aapl-20260627.htm | 2026-07-31 06:01:02 |
| 0000320193-26-000018 | 8-K | 2026-07-31T00:30:28.000Z | 2.02,9.01 | aapl-20260730.htm | 2026-07-30 16:30:28 |
| 0000320193-26-000005 | 8-K | 2026-01-30T02:30:33.000Z | 2.02,9.01 | aapl-20260129.htm | 2026-01-29 16:30:33 |

BRK.B（CIK 1067983）、GOOGL 和 GOOG（CIK 1652044）的 submissions 在用到的场景里写明。

#### Scenario: 带点的股票代码

- **GIVEN** CIK 1067983 的 submissions 有一份 2026-10-14T21:00:00.000Z 受理的 10-Q
- **WHEN** 取 BRK.B 自 2026-10-01 00:00Z 的公告
- **THEN** SEC 收到读 CIK 1067983 的 submissions 的请求
- **AND** 结果是这份 10-Q，标注的股票是 BRK.B

#### Scenario: 同一家公司的两只股票

- **GIVEN** CIK 1652044 的 submissions 有一份 2026-10-14T21:00:00.000Z 受理的 8-K
- **WHEN** 取 GOOGL、GOOG 自 2026-10-01 00:00Z 的公告
- **THEN** SEC 只收到一个读 CIK 1652044 的 submissions 的请求
- **AND** 结果是这份 8-K 一条，标注的股票依次是 GOOGL、GOOG

#### Scenario: 代码表只下载一次

- **GIVEN** 已经取过一次 AAPL 的公告
- **WHEN** 再取 AAPL 的公告
- **THEN** 这次 SEC 没有收到下载代码表的请求，只收到读 AAPL 的 submissions 的请求

#### Scenario: 查不到时重新下载代码表

- **GIVEN** 已经下载过代码表，里面没有 NEWCO；SEC 现在的代码表里有 NEWCO，CIK 9999999
- **WHEN** 取 NEWCO 的公告
- **THEN** SEC 收到一次下载代码表的请求，然后收到读 CIK 9999999 的 submissions 的请求

#### Scenario: 仍然查不到的股票

- **GIVEN** SEC 的代码表里一直没有 ZZZZQQ
- **WHEN** 取 AAPL、ZZZZQQ 自 2026-07-30 00:00Z 的公告
- **THEN** 不报错，结果是 AAPL 的公告，没有标注 ZZZZQQ 的公告
- **WHEN** 再取 AAPL、ZZZZQQ 的公告
- **THEN** 这次 SEC 没有收到下载代码表的请求

### Requirement: submissions 的映射

适配器 SHALL 读每家公司的 submissions（`https://data.sec.gov/submissions/CIK##########.json`，CIK 补零到 10 位），从 filings.recent 里取受理时刻不早于起始时刻的每一行，按下表映射为一份公告（news-and-filings spec「新闻和公告的条目」）：

| 公告 | submissions |
| --- | --- |
| accession number | accessionNumber |
| 标注的股票 | 调用方给出的股票里属于这家公司的，按调用方给出的顺序 |
| 表单类型 | form，原样 |
| item | items 按逗号分开；为空时没有 item |
| 受理时刻 | acceptanceDateTime，按「受理时刻的换算」换算 |
| 主文件地址 | `https://www.sec.gov/Archives/edgar/data/<CIK，不补零>/<accession number 去掉横线>/<primaryDocument>` |

全部表单类型都取，不筛选。

#### Scenario: 取 AAPL 的公告

- **WHEN** 取 AAPL 自 2026-07-30 00:00Z 的公告
- **THEN** SEC 收到下载代码表的请求和读 `https://data.sec.gov/submissions/CIK0000320193.json` 的请求
- **AND** 结果有三份：0000320193-26-000018（8-K）、0000320193-26-000020（10-Q）、0001140361-26-039785（4），都标注 AAPL；没有 0000320193-26-000005

#### Scenario: 8-K 的映射

- **WHEN** 取 AAPL 自 2026-07-30 00:00Z 的公告
- **THEN** 0000320193-26-000018 的表单类型 8-K，item 依次是 2.02、9.01，受理时刻 2026-07-30 20:30:28Z，主文件地址 `https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm`

#### Scenario: 不是 8-K 的公告的映射

- **WHEN** 取 AAPL 自 2026-07-30 00:00Z 的公告
- **THEN** 0001140361-26-039785 的表单类型 4，没有 item，受理时刻 2026-10-15 22:30:17Z，主文件地址 `https://www.sec.gov/Archives/edgar/data/320193/000114036126039785/xslF345X06/form4.xml`

#### Scenario: 受理时刻正好等于起始时刻

- **WHEN** 取 AAPL 自 2026-07-30 20:30:28Z 的公告
- **THEN** 结果里有 0000320193-26-000018
- **WHEN** 取 AAPL 自 2026-07-30 20:30:29Z 的公告
- **THEN** 结果里没有 0000320193-26-000018

### Requirement: 受理时刻的换算

submissions 里的 acceptanceDateTime 虽然以 Z 结尾，SEC 实际给出的值比真实的受理时刻晚了美东时间和 UTC 的时差（2026-10-17 对照公告页面实测：夏令时晚 4 小时，冬令时晚 5 小时）。适配器 SHALL 换算出真实的受理时刻：把这个值换成美东时间，它的钟面读数就是真实受理时刻的 UTC 钟面读数。换算按那一天是夏令时还是冬令时，不依赖本机时区。

#### Scenario: 夏令时受理的公告

- **WHEN** 取 AAPL 自 2026-07-30 00:00Z 的公告
- **THEN** 0000320193-26-000018 的受理时刻是 2026-07-30 20:30:28Z（美东 16:30:28），不是 2026-07-31 00:30:28Z

#### Scenario: 冬令时受理的公告

- **WHEN** 取 AAPL 自 2026-01-01 00:00Z 的公告
- **THEN** 0000320193-26-000005 的受理时刻是 2026-01-29 21:30:33Z（美东 16:30:33）

### Requirement: 同一份公告列在几家公司下

同一个 accession number 出现在这次读到的几家公司的 submissions 里时（比如并购的公告同时列在双方名下），适配器 SHALL 只给出一份公告，标注的股票是这几家公司的股票合在一起，按调用方给出的顺序。

#### Scenario: 并购双方都列了同一份公告

- **GIVEN** 代码表里 AAA 是 CIK 1、BBB 是 CIK 2；两家的 submissions 都有 0000000003-26-000003（425），2026-10-14T20:00:00.000Z 受理
- **WHEN** 取 AAA、BBB 自 2026-10-01 00:00Z 的公告
- **THEN** 结果里 0000000003-26-000003 只有一份，标注的股票依次是 AAA、BBB

### Requirement: User-Agent 和请求间隔

适配器发往 SEC 的每个请求 SHALL 带上配置的 User-Agent（SEC 要求写明联系方式）。

同一进程里发往 SEC 的请求（下载代码表和读 submissions）SHALL 一个接一个地发，不同时进行，包括几个调用同时取公告的时候；每个请求结束以后，至少等 1 / 每秒最多请求数 秒才发下一个，这样任何 1 秒里请求数都不超过配置的每秒最多请求数。

#### Scenario: 请求带上 User-Agent

- **GIVEN** User-Agent 配置为 U1
- **WHEN** 取 AAPL 自 2026-07-30 00:00Z 的公告
- **THEN** SEC 收到的两个请求都带有 User-Agent U1

#### Scenario: 请求之间隔开

- **WHEN** 第一次取 AAPL、BRK.B 自 2026-07-30 00:00Z 的公告
- **THEN** SEC 依次收到三个请求：下载代码表、读 AAPL 的 submissions、读 BRK.B 的 submissions
- **AND** 每个请求结束以后都等了 1 / 每秒最多请求数 秒，下一个请求才发出

#### Scenario: 两个调用同时取

- **GIVEN** 已经下载过代码表
- **WHEN** 两个调用同时取，一个取 AAPL 的公告，一个取 BRK.B 的公告
- **THEN** SEC 收到两个请求，第二个在第一个结束、并且等了 1 / 每秒最多请求数 秒以后才发出

### Requirement: 公告请求失败的分类

适配器 SHALL 按下表处理调用 SEC 的失败：

| 情况 | 结果 |
| --- | --- |
| 超时、连不上、连接中断；SEC 返回 403（超过频率限制、或者 User-Agent 不合要求时 SEC 都返回 403）、429 或 5xx | 数据源不可用，带上请求和状态码或错误 |
| SEC 返回其他 4xx（比如 404） | 原样报错：是程序的问题，不当作数据源不可用 |
| 回复的内容不是约定的格式 | 原样报错 |

读几家公司途中任何一个请求失败，整次调用按这个请求的失败处理，前面已经读到的公告不返回。适配器 MUST NOT 自己重试。

#### Scenario: SEC 超时

- **WHEN** 取 AAPL 的公告，读 submissions 时 SEC 超过超时时长没有响应
- **THEN** 报数据源不可用

#### Scenario: 连不上 SEC

- **WHEN** 取 AAPL 的公告，下载代码表时连不上 SEC
- **THEN** 报数据源不可用

#### Scenario: SEC 拒绝

- **WHEN** 取 AAPL 的公告，SEC 返回 403
- **THEN** 报数据源不可用，带上状态码 403

#### Scenario: SEC 限频

- **WHEN** 取 AAPL 的公告，SEC 返回 429
- **THEN** 报数据源不可用，SEC 只收到这一个请求

#### Scenario: SEC 服务端错误

- **WHEN** 取 AAPL 的公告，SEC 返回 503
- **THEN** 报数据源不可用

#### Scenario: SEC 查不到

- **WHEN** 取 AAPL 的公告，读 submissions 时 SEC 返回 404
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 代码表格式不对

- **WHEN** 取 AAPL 的公告，下载到的代码表缺了 cik_str
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 读到一半失败

- **GIVEN** 已经下载过代码表；读 AAPL 的 submissions 正常，读 BRK.B 的返回 503
- **WHEN** 取 AAPL、BRK.B 自 2026-07-30 00:00Z 的公告
- **THEN** 报数据源不可用，不返回 AAPL 的公告

### Requirement: SEC 配置

适配器 SHALL 可以配置：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| User-Agent | 写明联系方式，由人填写；部署时放在环境变量 `SEC_USER_AGENT` 里 | 无，必须给出 |
| 超时时长 | 每次请求等多久没有响应就算超时 | 10 秒 |
| 每秒最多请求数 | 见「User-Agent 和请求间隔」；SEC 的上限是每秒 10 次 | 8 |

User-Agent MUST NOT 为空，超时时长 MUST 大于 0，每秒最多请求数 MUST 在 1 到 10 之间，否则配置报错。

#### Scenario: SEC 配置非法

- **WHEN** User-Agent 为空字符串；或者超时时长为 0 秒；或者每秒最多请求数为 0 或 11
- **THEN** 配置报错
