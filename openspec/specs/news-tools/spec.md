# news-tools Specification

## Purpose
规定 News 查新闻的两个只读工具：参数、范围、补抓、结果、可以引用的内容和配置，让 News 只看到任务股票在快照时刻以前的新闻，补抓旧新闻也不影响事件检测。

## Requirements

### Requirement: 新闻工具

向真实模型调用 News 时，SHALL 给出两个读取工具（model-adapter spec「读取工具」）：

| 工具 | 作用 |
| --- | --- |
| `list_news` | 列出任务股票的新闻 |
| `read_news` | 读一条新闻的全文 |

- 工具的参数 MUST NOT 有股票和快照时刻：股票是任务的股票，时刻是这次运行读的这版快照的生成时刻，由程序给出
- 工具 SHALL 只读新闻：除了「补抓」写入新闻，不改变任何记录
- 工具次数按 sub-agent-calls spec「工具次数」算，两个工具合在一起
- Fundamentals、Quant、PM、Critic 和 Reviewer SHALL NOT 有这两个工具；替身不查询

本 spec 的场景除非另有说明，都使用下面的基准数据：

- T-2 是 News 的任务，股票 BBB，读 S1；S1 的生成时刻是 2026-10-14 15:00:00Z，时刻 T 指这个时刻
- 现在是 2026-10-14 16:30:00Z，事件检测的回看时长 24 小时，所以补抓的界线是 2026-10-13 16:30:00Z
- 新闻：

  | 新闻 | 新闻 ID | 标注的股票 | 发布时刻 | 标题 | 摘要 | 正文 |
  | --- | --- | --- | --- | --- | --- | --- |
  | A1 | 1 | BBB | 2026-07-10 12:00:00Z | BBB old story | 空 | `<p>old</p>` |
  | A2 | 2 | BBB | 2026-10-01 12:00:00Z | BBB wins contract | Deal with ACME | `<p>合同</p>` |
  | A3 | 3 | BBB、CCC、DDD | 2026-10-10 12:00:00Z | BBB Q3 EPS beats | 空 | 空 |
  | A4 | 4 | BBB | 2026-10-14 14:00:00Z | BBB raises guidance | 空 | `<p>A &amp; B</p><p>第二段</p>` |
  | A5 | 5 | BBB | 2026-10-14 16:00:00Z | BBB after snapshot | 空 | `<p>later</p>` |
  | A6 | 6 | CCC | 2026-10-14 12:00:00Z | CCC news | 空 | `<p>ccc</p>` |

- 状态库已经存下 A1 到 A6，新闻源对任何范围都不给出还没存过的新闻
- 没有写明的配置都是默认配置

#### Scenario: News 有两个工具

- **WHEN** 调用 News 运行 T-2
- **THEN** 请求里有读取工具 `list_news`、`read_news`
- **AND** `list_news` 的参数只有起始日、结束日、关键词，`read_news` 的参数只有条目 ID

#### Scenario: Fundamentals 没有这两个工具

- **WHEN** 调用 Fundamentals 运行 K-1 的财报请求
- **THEN** 请求里没有 `list_news`、`read_news`

### Requirement: 列出新闻

`list_news` SHALL 有下面的参数，都是可选的：

| 参数 | 含义 |
| --- | --- |
| 起始日 | 美东日期，含这一天 |
| 结束日 | 美东日期，含这一天 |
| 关键词 | 标题或摘要里含这段文字的新闻，不分大小写；不查正文 |

范围 SHALL 这样定：

- 结束：结束日的美东 24:00 以前和时刻 T，取早的；不写结束日时是 T
- 起始：起始日的美东 00:00；不写起始日时是结束往前 `tool_default_days` 天
- 起始早于时刻 T 往前 `tool_max_days` 天时，裁到这个时刻并写明
- 起始晚于结束时不查，结果为空，并写明范围是空的

结果 SHALL 有范围内符合条件的新闻，发布时刻从晚到早，相同的按新闻 ID 从大到小，最多 `max_tool_rows` 条；一共多少条；说明。每条有：

| 项 | 内容 |
| --- | --- |
| 条目 ID | `news/<新闻 ID>` |
| 标题、摘要、稿件来源、发布时刻 | 原样 |
| 标注的股票数 | 标注的股票有几只 |
| 有没有正文 | 去掉 HTML 以后还有文字才算有 |

全部稿件来源都列出。范围内没有新闻时结果为空，没有说明的空结果只表示确实没有新闻。

#### Scenario: 不写参数

- **WHEN** 调用 `list_news`，不写参数
- **THEN** 结果依次是 A4、A3，一共 2 条，没有说明
- **AND** A3 的条目 ID 是 `news/3`，标注的股票数 3，没有正文；A4 有正文

#### Scenario: 美东日期的边界

- **GIVEN** 另有 BBB 的新闻 A7，发布时刻 2026-10-11 03:30:00Z（美东 10 月 10 日 23:30），A8，发布时刻 2026-10-11 04:00:00Z（美东 10 月 11 日 00:00）
- **WHEN** 调用 `list_news`，起始日 2026-10-01，结束日 2026-10-10
- **THEN** 结果依次是 A7、A3、A2，没有 A8

#### Scenario: 快照以后发布的看不到

- **WHEN** 调用 `list_news`，起始日 2026-10-14，结束日 2026-10-20
- **THEN** 结果只有 A4，没有 A5

#### Scenario: 别的股票看不到

- **WHEN** 调用 `list_news`，起始日 2026-10-14
- **THEN** 结果里没有 A6

#### Scenario: 关键词查标题和摘要

- **WHEN** 调用 `list_news`，起始日 2026-10-01，关键词 `eps`
- **THEN** 结果只有 A3
- **WHEN** 调用 `list_news`，起始日 2026-10-01，关键词 `acme`
- **THEN** 结果只有 A2

#### Scenario: 关键词不查正文

- **WHEN** 调用 `list_news`，起始日 2026-10-01，关键词 `第二段`
- **THEN** 结果为空，一共 0 条，没有说明

#### Scenario: 条数正好到上限

- **GIVEN** `max_tool_rows` 是 2
- **WHEN** 调用 `list_news`，起始日 2026-10-10
- **THEN** 结果依次是 A4、A3，一共 2 条，没有说明

#### Scenario: 条数超过上限

- **GIVEN** `max_tool_rows` 是 2
- **WHEN** 调用 `list_news`，起始日 2026-10-01
- **THEN** 结果依次是 A4、A3，一共 3 条
- **AND** 说明写明这里只有最新的 2 条，缩小日期或者加关键词可以看到别的

#### Scenario: 起始早于可查范围

- **WHEN** 调用 `list_news`，起始日 2026-07-01
- **THEN** 结果依次是 A4、A3、A2，没有 A1
- **AND** 说明写明 2026-07-16 15:00:00Z 以前的新闻查不到

#### Scenario: 结束早于可查范围

- **WHEN** 调用 `list_news`，结束日 2026-07-01
- **THEN** 结果为空，说明写明范围是空的
- **AND** 没有向新闻源取

#### Scenario: 起始日晚于结束日

- **WHEN** 调用 `list_news`，起始日 2026-10-10，结束日 2026-10-01
- **THEN** 结果为空，说明写明范围是空的
- **AND** 没有向新闻源取

#### Scenario: 没有新闻

- **GIVEN** 状态库没有 BBB 的新闻
- **WHEN** 调用 `list_news`
- **THEN** 结果为空，一共 0 条，没有说明

### Requirement: 补抓

`list_news` 的范围里，不晚于补抓的界线的部分 SHALL 每次调用都向新闻源取一次，存下还没存过的（news-and-filings spec「取新闻」），然后从状态库读出结果。

- 补抓的界线 SHALL 是现在减去事件检测的回看时长（event-detection spec）：界线以后的新闻由事件检测写入和判断，工具 MUST NOT 向新闻源取
- 范围的起始晚于界线时 SHALL NOT 向新闻源取
- 新闻源只问这只股票，起始是范围的起始，结束是范围的结束和界线里早的那个
- 新闻源报数据源不可用时，SHALL 照样从状态库读出结果，并写明这段时间的新闻可能不全；这次不重试
- 新闻源的其他错误和读写状态库的错误 SHALL 原样报错

#### Scenario: 向新闻源取的范围

- **WHEN** 调用 `list_news`，不写参数
- **THEN** 新闻源被问到一次：股票 BBB，起始 2026-10-07 15:00:00Z，结束 2026-10-13 16:30:00Z

#### Scenario: 补抓到的存下再读

- **GIVEN** 状态库没有 A2；新闻源给出 A2
- **WHEN** 调用 `list_news`，起始日 2026-10-01
- **THEN** 结果依次是 A4、A3、A2
- **AND** A2 存进了状态库

#### Scenario: 不取最近 24 小时

- **WHEN** 调用 `list_news`，起始日 2026-10-14
- **THEN** 没有向新闻源取，结果只有 A4

#### Scenario: 界线跟着事件检测的回看时长

- **GIVEN** 事件检测的回看时长是 48 小时
- **WHEN** 调用 `list_news`，不写参数
- **THEN** 新闻源被问到的结束是 2026-10-12 16:30:00Z

#### Scenario: 快照早于界线

- **GIVEN** 现在是 2026-10-16 15:00:00Z
- **WHEN** 调用 `list_news`，不写参数
- **THEN** 新闻源被问到的结束是 2026-10-14 15:00:00Z

#### Scenario: 新闻源不可用

- **GIVEN** 新闻源这次报数据源不可用
- **WHEN** 调用 `list_news`，不写参数
- **THEN** 结果依次是 A4、A3
- **AND** 说明写明 2026-10-07 15:00:00Z 到 2026-10-13 16:30:00Z 的新闻可能不全

#### Scenario: 没有补抓时没有不全的说明

- **GIVEN** 新闻源这次会报数据源不可用
- **WHEN** 调用 `list_news`，起始日 2026-10-14
- **THEN** 结果只有 A4，没有说明

#### Scenario: 新闻源的其他错误

- **GIVEN** 新闻源这次报 key 不对
- **WHEN** 调用 `list_news`，不写参数
- **THEN** 原样报错

### Requirement: 读一条新闻

`read_news` SHALL 有一个参数：条目 ID，就是 `list_news` 给出的 `news/<新闻 ID>`。

结果 SHALL 有条目 ID、标题、摘要、稿件来源、标注的股票、发布时刻和正文：

- 正文 SHALL 去掉 HTML：标签去掉，实体换成字符，脚本和样式的内容不要，每个段落或换行一行，去掉空行和每行首尾的空白
- 去掉 HTML 以后没有文字时，正文为空，并写明这条新闻没有正文
- 新闻不存在、没有标注这只股票、发布时刻晚于时刻 T、条目 ID 的格式不对时，结果 SHALL 只写明查不到这条新闻，四种情况写法相同，MUST NOT 给出新闻的内容

#### Scenario: 读全文

- **WHEN** 调用 `read_news`，条目 ID `news/4`
- **THEN** 结果有 `news/4`、标题 BBB raises guidance、标注的股票 BBB、发布时刻 2026-10-14 14:00:00Z
- **AND** 正文是 `A & B`、换行、`第二段`

#### Scenario: 没有正文

- **WHEN** 调用 `read_news`，条目 ID `news/3`
- **THEN** 结果有标题，正文为空，写明这条新闻没有正文
- **AND** 标注的股票依次是 BBB、CCC、DDD

#### Scenario: 正文只有标签

- **GIVEN** 另有 BBB 的新闻 A7，发布时刻 2026-10-13 12:00:00Z，正文 `<img src="x.png">`
- **WHEN** 调用 `list_news`，起始日 2026-10-13
- **THEN** A7 没有正文
- **WHEN** 调用 `read_news`，条目 ID `news/7`
- **THEN** 正文为空，写明这条新闻没有正文

#### Scenario: 脚本和样式不要

- **GIVEN** 另有 BBB 的新闻 A7，发布时刻 2026-10-13 12:00:00Z，正文 `<style>p{}</style><script>x()</script><p>内容</p>`
- **WHEN** 调用 `read_news`，条目 ID `news/7`
- **THEN** 正文是 `内容`

#### Scenario: 查不到

- **WHEN** 调用 `read_news`，条目 ID 是 `news/6`（别的股票）、`news/5`（快照以后）、`news/999`（不存在）或 `s1/news/4`（格式不对）
- **THEN** 结果都相同，只写明查不到这条新闻

### Requirement: 工具返回的新闻可以引用

`list_news` 的结果里的每一条、`read_news` 读到的那一条 SHALL 算这次运行的工具返回过的内容（sub-agent-calls spec「工具返回过的内容」），ID 是条目 ID。

- 查不到的 MUST NOT 算
- 超过条数上限、没有进结果的 MUST NOT 算

#### Scenario: 列表里的新闻可以引用

- **GIVEN** 测试服务器第一次回答调用 `list_news`，不写参数；第二次回答调用 `submit_signal`，引用 `news/3`
- **WHEN** 经 Agent 网关运行 T-2
- **THEN** T-2 完成

#### Scenario: 读过的新闻可以引用

- **GIVEN** 测试服务器第一次回答调用 `read_news`，条目 ID `news/2`；第二次回答调用 `submit_signal`，引用 `news/2`
- **WHEN** 经 Agent 网关运行 T-2
- **THEN** T-2 完成

#### Scenario: 查不到的不能引用

- **GIVEN** 测试服务器第一次回答调用 `read_news`，条目 ID `news/5`；第二次回答调用 `submit_signal`，引用 `news/5`
- **WHEN** 经 Agent 网关运行 T-2
- **THEN** T-2 失败，原因是格式不合法，说明写明未知的条目 `news/5`

#### Scenario: 没进结果的不能引用

- **GIVEN** `max_tool_rows` 是 1；测试服务器第一次回答调用 `list_news`，不写参数；第二次回答调用 `submit_signal`，引用 `news/3`
- **WHEN** 经 Agent 网关运行 T-2
- **THEN** T-2 失败，原因是格式不合法，说明写明未知的条目 `news/3`

### Requirement: 新闻工具的配置

配置文件的 `news` 一节 SHALL 可以配置：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| `max_tool_rows` | `list_news` 一次最多列多少条 | 50 |
| `tool_default_days` | 不写起始日时往前多少天 | 7 |
| `tool_max_days` | 最早查到时刻 T 往前多少天 | 90 |

三项都 MUST 是正整数，否则配置报错。

#### Scenario: 配置不合法

- **WHEN** `max_tool_rows` 是 0；或者 `tool_max_days` 是 -1
- **THEN** 配置报错
