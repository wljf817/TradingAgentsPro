## ADDED Requirements

### Requirement: 受理时刻

submissions 里的 acceptanceDateTime SHALL 按它写明的 UTC 时刻读取，就是公告页面上写的受理时刻（2026-11-04 对照公告页面实测）。读取不依赖本机时区。

#### Scenario: 夏令时受理的公告

- **WHEN** 取 AAPL 自 2026-07-30 00:00Z 的公告
- **THEN** 0000320193-26-000018 的受理时刻是 2026-07-30 20:30:28Z（美东 16:30:28）

#### Scenario: 冬令时受理的公告

- **WHEN** 取 AAPL 自 2026-01-01 00:00Z 的公告
- **THEN** 0000320193-26-000005 的受理时刻是 2026-01-29 21:30:33Z（美东 16:30:33）

## MODIFIED Requirements

### Requirement: 股票代码到 CIK

适配器 SHALL 用 SEC 的股票代码表（`https://www.sec.gov/files/company_tickers.json`，每项有 cik_str 和 ticker）查每只股票所属公司的 CIK：

- 股票代码里的点换成横线再查：BRK.B 查 BRK-B
- 代码表在第一次需要时下载，之后一直用这一份
- 有股票在以前下载的代码表里查不到时，SHALL 重新下载一次代码表再查
- 刚下载的代码表里仍然查不到的股票没有公告，其他股票照常取，MUST NOT 报错；这只股票在本进程里不再触发重新下载
- 几只股票属于同一家公司（同一个 CIK）时，这家公司的公告列表只读一次，每份公告标注这几只股票

本 spec 的场景除非另有说明，都使用下面的基准数据，取自 2026-11-04 从 SEC 读到的数据。代码表里有：

| cik_str | ticker |
| --- | --- |
| 320193 | AAPL |
| 1067983 | BRK-B |
| 1652044 | GOOGL |
| 1652044 | GOOG |

AAPL（CIK 320193）的 submissions 里 filings.recent 的几行：

| accessionNumber | form | acceptanceDateTime | items | primaryDocument | 公告页面上写的受理时刻（美东） |
| --- | --- | --- | --- | --- | --- |
| 0001140361-26-039785 | 4 | 2026-10-15T22:30:17.000Z | 空 | xslF345X06/form4.xml | 2026-10-15 18:30:17 |
| 0000320193-26-000020 | 10-Q | 2026-07-31T10:01:02.000Z | 空 | aapl-20260627.htm | 2026-07-31 06:01:02 |
| 0000320193-26-000018 | 8-K | 2026-07-30T20:30:28.000Z | 2.02,9.01 | aapl-20260730.htm | 2026-07-30 16:30:28 |
| 0000320193-26-000005 | 8-K | 2026-01-29T21:30:33.000Z | 2.02,9.01 | aapl-20260129.htm | 2026-01-29 16:30:33 |

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
| 受理时刻 | acceptanceDateTime，见「受理时刻」 |
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

## REMOVED Requirements

### Requirement: 受理时刻的换算

**Reason**: SEC 的 acceptanceDateTime 现在就是真实的 UTC 时刻，不用换算。

**Migration**: 由「受理时刻」代替。
