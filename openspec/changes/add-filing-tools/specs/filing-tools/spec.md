## Purpose

Fundamentals 的三个读取工具：列出任务股票能读的公告、读一份公告里的文件、读财报电话会文字稿。工具只返回快照时刻以前的内容，缺的向数据源补抓并存下。

## ADDED Requirements

### Requirement: 公告工具

向真实模型调用 Fundamentals 时，SHALL 给出三个读取工具（model-adapter spec「读取工具」）：

| 工具 | 作用 |
| --- | --- |
| `list_filings` | 列出任务股票能读的公告 |
| `read_filing` | 读一份公告里的一个文件，或者在文件里查找一段文字 |
| `read_transcript` | 读一篇财报电话会文字稿 |

- 工具的参数 MUST NOT 有股票和快照时刻：股票是任务的股票，时刻是这次运行读的这版快照的生成时刻，由程序给出
- 工具 SHALL 只读：除了「公告的补抓」写入公告、「读电话会文字稿」的补抓写入新闻、`read_filing` 存下公告的文件，不改变任何记录
- 工具次数按 sub-agent-calls spec「工具次数」算，三个工具合在一起
- News、Quant、PM、Critic 和 Reviewer SHALL NOT 有这三个工具；替身不查询

能读的表单 SHALL 是 8-K、6-K、10-Q、10-K、20-F，以及它们的修正版（表单类型后面加 `/A`，比如 8-K/A）；修正版按它的原表单算。

本 spec 的场景除非另有说明，都使用下面的基准数据：

- T-4 是 Fundamentals 的任务，股票 BBB，读 S1；S1 的生成时刻是 2026-10-14 15:00:00Z，时刻 T 指这个时刻
- 现在是 2026-10-14 16:30:00Z，事件检测的回看时长 24 小时，所以补抓的界线是 2026-10-13 16:30:00Z
- 股票池里有 BBB，没有 CCC
- 公告：

  | 公告 | accession number | 标注的股票 | 表单类型 | item | 受理时刻 |
  | --- | --- | --- | --- | --- | --- |
  | G1 | 0000000002-25-000001 | BBB | 10-K | 无 | 2025-10-01 12:00:00Z |
  | G2 | 0000000002-26-000002 | BBB | 10-K/A | 无 | 2026-03-02 12:00:00Z |
  | G3 | 0000000002-26-000003 | BBB | 10-Q | 无 | 2026-08-03 12:00:00Z |
  | G4 | 0000000002-26-000004 | BBB | 424B2 | 无 | 2026-10-10 12:00:00Z |
  | G5 | 0000000002-26-000005 | BBB | 8-K | 2.02、9.01 | 2026-10-13 20:30:00Z |
  | G6 | 0000000002-26-000006 | BBB | 4 | 无 | 2026-10-14 01:00:00Z |
  | G7 | 0000000002-26-000007 | BBB | 8-K | 7.01 | 2026-10-14 16:00:00Z |
  | G8 | 0000000003-26-000008 | CCC | 8-K | 2.02 | 2026-10-12 20:00:00Z |

- 状态库已经存下 G1 到 G8，公告源对任何范围都不给出还没存过的公告
- 没有写明的配置都是默认配置

#### Scenario: Fundamentals 有三个工具

- **WHEN** 调用 Fundamentals 运行 T-4
- **THEN** 请求里有读取工具 `list_filings`、`read_filing`、`read_transcript`，没有 `list_news`、`read_news`
- **AND** `list_filings` 的参数只有表单类型、起始日、结束日，`read_filing` 的参数只有条目 ID、文件、起点、查找，`read_transcript` 的参数只有截止日

#### Scenario: News 没有这三个工具

- **WHEN** 调用 News 运行 BBB 的新闻任务
- **THEN** 请求里没有 `list_filings`、`read_filing`、`read_transcript`

### Requirement: 列出公告

`list_filings` SHALL 有下面的参数，都是可选的：

| 参数 | 含义 |
| --- | --- |
| 表单类型 | 8-K、6-K、10-Q、10-K、20-F 之一，不分大小写；修正版一起列出 |
| 起始日 | 美东日期，含这一天 |
| 结束日 | 美东日期，含这一天 |

范围 SHALL 这样定：

- 结束：结束日的美东 24:00 以前和时刻 T，取早的；不写结束日时是 T
- 最早：时刻 T 往前 `filing_tool_days` 天；不写起始日时起始就是它
- 起始日的美东 00:00 早于最早时，裁到最早并写明
- 起始晚于结束时不查，结果为空，并写明范围是空的
- 表单类型不是这五种之一时不查，结果为空，并写明能写哪几种

结果 SHALL 有范围内受理、表单能读、标注了这只股票的公告，受理时刻从晚到早，相同的按 accession number 从大到小，最多 `max_filing_rows` 份；一共多少份；说明。每份有：条目 ID `filing/<accession number>`、表单类型、item、受理时刻。范围内没有公告时结果为空，没有说明的空结果只表示确实没有。

#### Scenario: 不写参数

- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 结果依次是 G5、G3、G2，一共 3 份，没有说明
- **AND** G5 的条目 ID 是 `filing/0000000002-26-000005`，item 依次是 2.02、9.01

#### Scenario: 不能读的表单不列

- **WHEN** 调用 `list_filings`，起始日 2026-10-01
- **THEN** 结果只有 G5，没有 G4 和 G6

#### Scenario: 按表单类型筛

- **WHEN** 调用 `list_filings`，表单类型 `10-k`
- **THEN** 结果只有 G2
- **WHEN** 调用 `list_filings`，表单类型 `8-K`
- **THEN** 结果只有 G5

#### Scenario: 表单类型写错

- **WHEN** 调用 `list_filings`，表单类型 `424B2`
- **THEN** 结果为空，说明写明表单类型只能是 8-K、6-K、10-Q、10-K、20-F
- **AND** 没有向公告源取

#### Scenario: 美东日期的边界

- **GIVEN** 另有 BBB 的 8-K 公告 G9，受理时刻 2026-10-03 03:30:00Z（美东 10 月 2 日 23:30），G10，受理时刻 2026-10-03 04:00:00Z（美东 10 月 3 日 00:00）
- **WHEN** 调用 `list_filings`，起始日 2026-08-01，结束日 2026-10-02
- **THEN** 结果依次是 G9、G3，没有 G10

#### Scenario: 快照以后受理的看不到

- **WHEN** 调用 `list_filings`，起始日 2026-10-14
- **THEN** 结果为空，没有 G7

#### Scenario: 别的股票看不到

- **WHEN** 调用 `list_filings`，起始日 2026-10-01
- **THEN** 结果里没有 G8

#### Scenario: 起始早于可查范围

- **WHEN** 调用 `list_filings`，起始日 2025-09-01
- **THEN** 结果依次是 G5、G3、G2，没有 G1
- **AND** 说明写明 2025-10-14 15:00:00Z 以前的公告查不到

#### Scenario: 起始日晚于结束日

- **WHEN** 调用 `list_filings`，起始日 2026-10-10，结束日 2026-10-01
- **THEN** 结果为空，说明写明范围是空的
- **AND** 没有向公告源取

#### Scenario: 条数正好到上限

- **GIVEN** `max_filing_rows` 是 3
- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 结果依次是 G5、G3、G2，一共 3 份，没有说明

#### Scenario: 条数超过上限

- **GIVEN** `max_filing_rows` 是 2
- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 结果依次是 G5、G3，一共 3 份
- **AND** 说明写明这里只有最新的 2 份，缩小日期或者按表单类型筛可以看到别的

#### Scenario: 没有公告

- **GIVEN** 状态库没有 BBB 的公告
- **WHEN** 调用 `list_filings`
- **THEN** 结果为空，一共 0 份，没有说明

### Requirement: 公告的补抓

`list_filings` SHALL 每次调用都先向公告源取一次（news-and-filings spec「取公告」），存下还没存过的，然后从状态库读出结果：

- 只问这只股票；起始是范围的起始；表单只要能读的那几种
- 结束：股票现在在股票池里时，是范围的结束和补抓的界线里早的那个，界线是现在减去事件检测的回看时长（event-detection spec），界线以后的公告由事件检测写入和判断；股票不在股票池里时，是范围的结束
- 起始晚于结束时 SHALL NOT 向公告源取
- 公告源报数据源不可用时，SHALL 照样从状态库读出结果，并写明这段时间的公告可能不全；这次不重试
- 公告源的其他错误和读写状态库的错误 SHALL 原样报错

#### Scenario: 向公告源取的范围

- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 公告源被问到一次：股票 BBB，起始 2025-10-14 15:00:00Z，结束 2026-10-13 16:30:00Z，表单是能读的那几种

#### Scenario: 补抓到的存下再读

- **GIVEN** 状态库没有 G3；公告源给出 G3 和 BBB 的 424B2 公告 G11（受理时刻 2026-08-04 12:00:00Z）
- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 结果依次是 G5、G3、G2
- **AND** G3 存进了状态库，G11 没有存

#### Scenario: 股票池里的股票不取最近 24 小时

- **WHEN** 调用 `list_filings`，起始日 2026-10-14
- **THEN** 没有向公告源取

#### Scenario: 不在股票池的股票取到时刻 T

- **GIVEN** T-4 的股票是 CCC
- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 公告源被问到的股票是 CCC，结束是 2026-10-14 15:00:00Z
- **AND** 结果只有 G8

#### Scenario: 界线跟着事件检测的回看时长

- **GIVEN** 事件检测的回看时长是 48 小时
- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 公告源被问到的结束是 2026-10-12 16:30:00Z

#### Scenario: 公告源不可用

- **GIVEN** 公告源这次报数据源不可用
- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 结果依次是 G5、G3、G2
- **AND** 说明写明 2025-10-14 15:00:00Z 到 2026-10-13 16:30:00Z 的公告可能不全

#### Scenario: 公告源的其他错误

- **GIVEN** 公告源这次报 SEC 返回 404
- **WHEN** 调用 `list_filings`，不写参数
- **THEN** 原样报错

### Requirement: 读公告

`read_filing` SHALL 有下面的参数：

| 参数 | 含义 |
| --- | --- |
| 条目 ID | `list_filings` 给出的 `filing/<accession number>`，必填 |
| 文件 | 文件类型，就是结果里文件列表写的类型，比如 EX-99.1；不写时是主文件 |
| 起点 | 文件纯文本里的字符位置，从 0 算；不写时是 0 |
| 查找 | 一段文字；写了就在文件里查找，不给正文 |

公告的文件按 news-and-filings spec「公告的文件」取得：第一次读到时向公告源取、存下，之后只读状态库。

结果 SHALL 有条目 ID、表单类型、item、受理时刻，以及文件列表：每个文件的类型、文件名和字数，按公告里的顺序，第一个是主文件。然后：

- 不写查找时：从起点起最多 `max_filing_chars` 个字；写明文件一共多少字；后面还有时写明下一次从哪个位置读起；起点不小于字数时正文为空，并写明起点超过了文件的字数
- 写了查找时：从起点往后，不分大小写、把 ’ 当作 ' 查找这段文字，按位置先后给出最多 `max_filing_matches` 处，每处有位置和所在的那一行（超过 300 字的只给前 300 字）；写明从起点往后一共多少处；超过上限时写明还有更多，可以从最后一处之后接着查

下面的情况 SHALL 只给出说明：

- 公告不存在、没有标注这只股票、受理时刻晚于时刻 T、表单不能读、条目 ID 的格式不对时，只写明查不到这份公告，五种情况写法相同，MUST NOT 给出公告的内容
- 文件列表里没有这个类型时，写明没有这个文件，并给出文件列表
- 公告源报数据源不可用时，写明现在取不到这份公告的文件，可以稍后再试；公告源的其他错误原样报错
- 公告源给出的文件列表是空的时，写明这份公告没有能读的文件

本 requirement 的场景里：

- G5 的文件依次是：8-K `bbb-20261013.htm`，1,200 字；EX-99.1 `ex991.htm`，25,000 字；EX-99.2 `ex992.htm`，8,000 字
- G3 的文件只有 10-Q `bbb-20260630.htm`，120,000 字；`Management’s Discussion and Analysis` 出现在第 1,000 字（目录）和第 40,000 字，`MANAGEMENT'S DISCUSSION AND ANALYSIS` 出现在第 90,000 字，三处各在一行里
- 状态库还没有存下这两份公告的文件，公告源给出上面的文件

#### Scenario: 读主文件

- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000005`
- **THEN** 结果有 G5 的表单类型 8-K、item 2.02 和 9.01、受理时刻，文件列表依次是 8-K、EX-99.1、EX-99.2 和各自的文件名、字数
- **AND** 正文是主文件的全部 1,200 字，写明一共 1,200 字，没有写下一次的位置
- **AND** G5 的三个文件存进了状态库

#### Scenario: 读附件

- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000005`，文件 `EX-99.1`
- **THEN** 正文是 EX-99.1 第 0 字到第 19,999 字，写明一共 25,000 字，下一次从 20,000 读起
- **WHEN** 再调用 `read_filing`，同一条目 ID，文件 `EX-99.1`，起点 20,000
- **THEN** 正文是第 20,000 字到第 24,999 字，没有写下一次的位置
- **AND** 公告源只被问过一次

#### Scenario: 起点超过字数

- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000005`，文件 `EX-99.2`，起点 8,000
- **THEN** 正文为空，写明起点超过了文件的字数，一共 8,000 字

#### Scenario: 没有这个文件

- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000005`，文件 `EX-13`
- **THEN** 写明没有这个文件，并给出 8-K、EX-99.1、EX-99.2 的文件列表

#### Scenario: 查找

- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000003`，查找 `management's discussion`
- **THEN** 结果依次是位置 1,000、40,000、90,000 三处和各自那一行，一共 3 处，没有正文

#### Scenario: 从起点往后查找

- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000003`，起点 30,000，查找 `MANAGEMENT’S DISCUSSION`
- **THEN** 结果依次是位置 40,000、90,000，一共 2 处

#### Scenario: 查找的处数超过上限

- **GIVEN** `max_filing_matches` 是 2
- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000003`，查找 `management's discussion`
- **THEN** 结果依次是位置 1,000、40,000，一共 3 处，写明还有更多

#### Scenario: 查不到

- **WHEN** 调用 `read_filing`，条目 ID 是 `filing/0000000003-26-000008`（别的股票）、`filing/0000000002-26-000007`（快照以后）、`filing/0000000002-26-000004`（424B2）、`filing/0000000009-26-000099`（不存在）或 `s1/filing/0000000002-26-000005`（格式不对）
- **THEN** 结果都相同，只写明查不到这份公告
- **AND** 没有向公告源取

#### Scenario: 公告源不可用

- **GIVEN** 公告源这次取文件报数据源不可用
- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000005`
- **THEN** 写明现在取不到这份公告的文件，可以稍后再试
- **AND** 状态库没有存下 G5 的文件

#### Scenario: 没有能读的文件

- **GIVEN** 公告源给出 G5 的文件列表是空的
- **WHEN** 调用 `read_filing`，条目 ID `filing/0000000002-26-000005`
- **THEN** 写明这份公告没有能读的文件

### Requirement: 读电话会文字稿

`read_transcript` SHALL 有一个可选参数：截止日，美东日期，含这一天。

查找的范围 SHALL 这样定：

- 结束：截止日的美东 24:00 以前和时刻 T，取早的；不写截止日时是 T
- 起始：结束往前 `transcript_window_days` 天
- 最早：时刻 T 往前 `transcript_max_days` 天；起始早于最早时裁到最早并写明；结束早于最早时不查，结果为空，并写明范围是空的

文字稿 SHALL 是范围内发布、标注了这只股票的新闻里，同时满足下面三条的：

- 标题含 `transcript`，不分大小写
- 标题含 `earnings call` 或 `earnings conference call`，不分大小写
- 存下的正文（HTML 原样）至少 `min_transcript_chars` 个字符

结果 SHALL 是其中发布时刻最晚的一篇，相同的取新闻 ID 大的：条目 ID `news/<新闻 ID>`、标题、发布时刻，正文按 news-tools spec「读一条新闻」的规则去掉 HTML。范围内没有文字稿时结果为空，写明这段时间没有找到文字稿和范围的两端。

范围里不晚于补抓的界线的部分 SHALL 按 news-tools spec「补抓」的规则向新闻源补抓：只问这只股票，起始是范围的起始，结束是范围的结束和界线里早的那个；新闻源报数据源不可用时照样从状态库找，并写明这段时间的新闻可能不全。

本 requirement 的场景里，状态库存下了下面的新闻，新闻源对任何范围都不给出还没存过的新闻：

| 新闻 | 新闻 ID | 标注的股票 | 发布时刻 | 标题 | 正文字符数 |
| --- | --- | --- | --- | --- | --- |
| A1 | 11 | BBB | 2026-07-20 22:00:00Z | BBB Q2 2026 Earnings Call Transcript | 12,000 |
| A2 | 12 | BBB | 2026-10-12 12:00:00Z | BBB beats Q3 estimates | 20,000 |
| A3 | 13 | BBB | 2026-10-13 22:00:00Z | Transcript: BBB Q3 2026 Earnings Conference Call | 15,000 |
| A4 | 14 | BBB | 2026-10-14 10:00:00Z | BBB Q3 2026 Earnings Call Transcript (CORRECTED) | 1,500 |
| A5 | 15 | BBB | 2026-10-14 16:00:00Z | BBB Q3 Earnings Call Transcript | 15,000 |
| A6 | 16 | CCC | 2026-10-13 22:00:00Z | CCC Q3 Earnings Call Transcript | 15,000 |

#### Scenario: 不写截止日

- **WHEN** 调用 `read_transcript`，不写参数
- **THEN** 结果是 A3：条目 ID `news/13`、标题、发布时刻 2026-10-13 22:00:00Z、去掉 HTML 的正文
- **AND** 新闻源被问到一次：股票 BBB，起始 2026-07-06 15:00:00Z，结束 2026-10-13 16:30:00Z

#### Scenario: 不是文字稿的不算

- **WHEN** 调用 `read_transcript`，截止日 2026-10-12
- **THEN** 结果是 A1，不是 A2

#### Scenario: 太短的不算

- **GIVEN** 状态库没有 A3
- **WHEN** 调用 `read_transcript`，不写参数
- **THEN** 结果是 A1，不是 A4

#### Scenario: 快照以后发布的和别的股票的看不到

- **GIVEN** T-4 的股票是 CCC
- **WHEN** 调用 `read_transcript`，不写参数
- **THEN** 结果是 A6
- **GIVEN** T-4 的股票是 BBB，状态库没有 A1、A3
- **WHEN** 调用 `read_transcript`，不写参数
- **THEN** 结果为空，写明 2026-07-06 15:00:00Z 到 2026-10-14 15:00:00Z 没有找到文字稿，没有 A5

#### Scenario: 起始早于可查范围

- **WHEN** 调用 `read_transcript`，截止日 2025-11-01
- **THEN** 结果为空，说明写明 2025-10-14 15:00:00Z 以前查不到

#### Scenario: 结束早于可查范围

- **WHEN** 调用 `read_transcript`，截止日 2025-10-01
- **THEN** 结果为空，说明写明范围是空的
- **AND** 没有向新闻源取

#### Scenario: 新闻源不可用

- **GIVEN** 新闻源这次报数据源不可用
- **WHEN** 调用 `read_transcript`，不写参数
- **THEN** 结果是 A3
- **AND** 说明写明 2026-07-06 15:00:00Z 到 2026-10-13 16:30:00Z 的新闻可能不全

### Requirement: 工具返回的内容可以引用

下面这些 SHALL 算这次运行的工具返回过的内容（sub-agent-calls spec「工具返回过的内容」），ID 是条目 ID：

- `list_filings` 的结果里的每一份公告
- `read_filing` 读到正文或者查找到结果的那份公告，查到 0 处也算
- `read_transcript` 给出的那篇文字稿

查不到的、只给出说明的、超过条数上限没有进结果的 MUST NOT 算。

#### Scenario: 列表里的公告可以引用

- **GIVEN** 测试服务器第一次回答调用 `list_filings`，不写参数；第二次回答调用 `submit_signal`，引用 `filing/0000000002-26-000003`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 完成

#### Scenario: 读过的公告和文字稿可以引用

- **GIVEN** 测试服务器第一次回答调用 `read_filing`，条目 ID `filing/0000000002-26-000005`；第二次回答调用 `read_transcript`；第三次回答调用 `submit_signal`，引用 `filing/0000000002-26-000005` 和 `news/13`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 完成

#### Scenario: 查不到的不能引用

- **GIVEN** 测试服务器第一次回答调用 `read_filing`，条目 ID `filing/0000000002-26-000007`；第二次回答调用 `submit_signal`，引用 `filing/0000000002-26-000007`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 失败，原因是格式不合法，说明写明未知的条目 `filing/0000000002-26-000007`

#### Scenario: 没进结果的不能引用

- **GIVEN** `max_filing_rows` 是 1；测试服务器第一次回答调用 `list_filings`，不写参数；第二次回答调用 `submit_signal`，引用 `filing/0000000002-26-000003`
- **WHEN** 经 Agent 网关运行 T-4
- **THEN** T-4 失败，原因是格式不合法，说明写明未知的条目 `filing/0000000002-26-000003`

### Requirement: 公告工具的配置

配置文件的 `filings` 一节 SHALL 可以配置：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| `max_filing_rows` | `list_filings` 一次最多列多少份 | 50 |
| `filing_tool_days` | `list_filings` 最早查到时刻 T 往前多少天，也是不写起始日时的范围 | 365 |
| `max_filing_chars` | `read_filing` 一次最多给多少字 | 20000 |
| `max_filing_matches` | `read_filing` 查找一次最多给多少处 | 20 |
| `transcript_window_days` | `read_transcript` 从结束往前找多少天 | 100 |
| `transcript_max_days` | `read_transcript` 最早查到时刻 T 往前多少天 | 365 |
| `min_transcript_chars` | 文字稿的正文至少多少个字符 | 10000 |

各项都 MUST 是正整数，否则配置报错。

#### Scenario: 配置不合法

- **WHEN** `max_filing_rows` 是 0；或者 `max_filing_chars` 是 -1；或者 `transcript_window_days` 是 0
- **THEN** 配置报错
