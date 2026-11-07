## ADDED Requirements

### Requirement: 财务事实

适配器 SHALL 能给出一只股票所属公司的财务事实（company-data spec「财务事实的记录」）：

- 按「股票代码到 CIK」查 CIK；刚下载的代码表里仍然查不到时，结果是没有财务事实
- 读 `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`（CIK 补零到 10 位）；SEC 对这个地址返回 404 时，结果是没有财务事实，MUST NOT 报错
- 取 `facts.us-gaap` 下要的科目，每个单位下有 `start` 的每一项映射为一条事实：start、end 是起始日、结束日，val 是数值，accn 是 accession number，form 是表单类型，filed 是提交日；股票是调用方给出的代码，科目和单位是它所在的键
- 没有 `start` 的项（某一天的余额）不取；没有 `us-gaap` 或者没有要的科目时，结果为空
- 请求的节奏照「User-Agent 和请求间隔」，和公告的请求一个接一个地发；其他失败按「公告请求失败的分类」处理

本 requirement 的场景里，AAPL（CIK 320193）的 companyfacts 是：

```json
{
  "cik": 320193,
  "facts": {
    "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
      {"end": "2026-10-17", "val": 14776353000, "accn": "0000320193-26-000031", "form": "10-K", "filed": "2026-10-31"}
    ]}}},
    "us-gaap": {
      "Revenues": {"units": {"USD": [
        {"start": "2026-06-28", "end": "2026-09-26", "val": 114477000000, "accn": "0000320193-26-000031", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-10-31", "frame": "CY2026Q3"}
      ]}},
      "EarningsPerShareDiluted": {"units": {"USD/shares": [
        {"start": "2026-03-29", "end": "2026-06-27", "val": 1.57, "accn": "0000320193-26-000020", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-07-31"}
      ]}},
      "Liabilities": {"units": {"USD": [
        {"end": "2026-09-26", "val": 285508000000, "accn": "0000320193-26-000031", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-10-31"}
      ]}},
      "AccountsPayableCurrent": {"units": {"USD": [
        {"start": "2026-06-28", "end": "2026-09-26", "val": 1, "accn": "0000320193-26-000031", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-10-31"}
      ]}}
    }
  }
}
```

#### Scenario: 取 AAPL 的财务事实

- **WHEN** 取 AAPL 的 Revenues、EarningsPerShareDiluted、Liabilities 的财务事实
- **THEN** SEC 收到读 `https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` 的请求
- **AND** 结果是两条：Revenues、USD、2026-06-28 到 2026-09-26、114477000000、0000320193-26-000031、10-K、提交日 2026-10-31；EarningsPerShareDiluted、USD/shares、2026-03-29 到 2026-06-27、1.57、0000320193-26-000020、10-Q、提交日 2026-07-31
- **AND** 没有 Liabilities（没有 start）、dei 的股数和 AccountsPayableCurrent（不是要的科目）

#### Scenario: 代码表里没有

- **GIVEN** SEC 的代码表里一直没有 ZZZZQQ
- **WHEN** 取 ZZZZQQ 的财务事实
- **THEN** 结果是没有财务事实，不报错；SEC 没有收到 companyfacts 的请求

#### Scenario: SEC 没有这家公司的财务事实

- **GIVEN** 代码表里有 SPY，CIK 884394；SEC 对它的 companyfacts 返回 404
- **WHEN** 取 SPY 的财务事实
- **THEN** 结果是没有财务事实，不报错

#### Scenario: 只有 IFRS 的公司

- **GIVEN** 代码表里有 TSM，CIK 1046179；它的 companyfacts 只有 `dei` 和 `ifrs-full`
- **WHEN** 取 TSM 的财务事实
- **THEN** 结果为空，不报错

#### Scenario: 取财务事实时 SEC 不可用

- **WHEN** 取 AAPL 的财务事实，SEC 返回 503
- **THEN** 报数据源不可用

#### Scenario: 和公告的请求错开

- **GIVEN** 每秒最多请求数是 8
- **WHEN** 取 AAPL 的公告，紧接着取 AAPL 的财务事实
- **THEN** 读 companyfacts 的请求在读 submissions 的请求结束 0.125 秒以后才发出
