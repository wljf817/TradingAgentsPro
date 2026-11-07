## MODIFIED Requirements

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

