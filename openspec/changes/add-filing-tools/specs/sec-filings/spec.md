## MODIFIED Requirements

### Requirement: User-Agent 和请求间隔

适配器发往 SEC 的每个请求 SHALL 带上配置的 User-Agent（SEC 要求写明联系方式）。

同一进程里发往 SEC 的请求（下载代码表、读 submissions、读公告的目录页和文件）SHALL 一个接一个地发，不同时进行，包括几个调用同时取公告的时候；每个请求结束以后，至少等 1 / 每秒最多请求数 秒才发下一个，这样任何 1 秒里请求数都不超过配置的每秒最多请求数。

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

#### Scenario: 读公告的文件也隔开

- **WHEN** 取 0000320193-26-000018 的文件
- **THEN** SEC 依次收到三个请求：目录页、主文件、EX-99.1，都带有 User-Agent
- **AND** 每个请求结束以后都等了 1 / 每秒最多请求数 秒，下一个请求才发出

## ADDED Requirements

### Requirement: 公告的文件

适配器 SHALL 能给出一份公告的文件（news-and-filings spec「公告的文件」）：

- 公告的目录页是主文件地址里 accession number 去掉横线的那一级目录下的 `<accession number>-index.htm`
- 从目录页的文件表读出每个文件的类型和文件名，按表里的顺序；表里的第一个文件是主文件
- 要读的文件是主文件，以及类型以 EX-99 或 EX-13 开头的附件；文件名以 `.htm`、`.html`、`.txt` 结尾的才读，别的（PDF、图片、XBRL）跳过
- 每个要读的文件下载一次，转成纯文本：去掉 `head`、`ix:header`、`script`、`style` 里的内容和全部标签，实体换成字符，不换行空格换成空格；`p`、`div`、`br`、`tr`、`li`、`h1` 到 `h6`、`table` 各起一行，`td`、`th` 前后各加一个空格；每行里连续的空格和制表符合成一个，去掉每行首尾的空白和空行。`.txt` 文件只去掉每行首尾的空白和空行
- 结果是文件的类型、文件名和纯文本，按目录页的顺序；没有要读的文件时结果为空
- 请求失败按「公告请求失败的分类」处理；任何一个请求失败，整次调用按这个请求的失败处理，不返回已经读到的文件

本 requirement 的场景里，0000320193-26-000018 的目录页是 `https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/0000320193-26-000018-index.htm`，文件表依次是：

| 类型 | 文件名 |
| --- | --- |
| 8-K | aapl-20260730.htm |
| EX-99.1 | a8-kex991q3202606272026.htm |
| EX-101.SCH | aapl-20260730.xsd |
| GRAPHIC | g1.jpg |
| （空） | 0000320193-26-000018.txt |

#### Scenario: 8-K 的文件

- **WHEN** 取 0000320193-26-000018 的文件
- **THEN** SEC 收到目录页、`aapl-20260730.htm`、`a8-kex991q3202606272026.htm` 三个请求
- **AND** 结果依次是 8-K `aapl-20260730.htm`、EX-99.1 `a8-kex991q3202606272026.htm`，各自的纯文本

#### Scenario: 10-K 的附件 13

- **GIVEN** 一份 10-K 的文件表依次是 10-K `wfc-10k.htm`、EX-13 `ex13.htm`、EX-21 `ex21.htm`、EX-99.1 `ex991.pdf`
- **WHEN** 取这份 10-K 的文件
- **THEN** 结果依次是 10-K、EX-13，没有 EX-21 和 PDF 的 EX-99.1

#### Scenario: 转成纯文本

- **GIVEN** 主文件是 `<html><head><title>x</title></head><body><ix:header><ix:hidden>隐藏</ix:hidden></ix:header><div>Revenue&nbsp;was   $1.2 billion</div><p>Net&amp;income</p><br><script>x()</script><table><tr><td>Q3</td><td>2026</td></tr></table></body></html>`
- **WHEN** 取这份公告的文件
- **THEN** 主文件的纯文本是 `Revenue was $1.2 billion`、`Net&income`、`Q3 2026` 三行

#### Scenario: 没有要读的文件

- **GIVEN** 一份 6-K 的文件表依次是 6-K `form.pdf`、GRAPHIC `g1.jpg`
- **WHEN** 取这份公告的文件
- **THEN** 结果为空

#### Scenario: 读文件时 SEC 不可用

- **WHEN** 取 0000320193-26-000018 的文件，读 EX-99.1 时 SEC 返回 503
- **THEN** 报数据源不可用，不返回主文件

#### Scenario: 目录页查不到

- **WHEN** 取 0000320193-26-000018 的文件，SEC 对目录页返回 404
- **THEN** 原样报错，不报数据源不可用
