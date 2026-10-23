# Spec Delta

## MODIFIED Requirements

### Requirement: 条目和条目 ID

快照里的每条事实 SHALL 是一个条目，带一个条目 ID。条目 ID 写成 `s<版本>/<类别>`，有键的写成 `s<版本>/<类别>/<键>`：

| 条目 | 类别 | 键 | 例子 |
| --- | --- | --- | --- |
| 账户 | account | 没有 | s1/account |
| 市场状态 | market | 没有 | s1/market |
| 股票池 | pool | 没有 | s1/pool |
| 一只股票的行情 | quote | 股票代码 | s1/quote/BBB |
| 一条新闻 | news | 新闻 ID | s1/news/102 |
| 一份公告 | filing | accession number | s1/filing/0000000002-26-000003 |
| 一次财报发布 | earnings | 股票代码、冒号、财年、Q、季度 | s1/earnings/BBB:2026Q3 |
| 一只股票的 surprise | surprises | 股票代码 | s1/surprises/BBB |
| 一个事件 | event | 事件 ID（event-rules spec「事件 ID 和去重」） | s1/event/BBB:2026-10-14:news |
| 粗筛结果 | screen | 没有 | s1/screen |

- 条目 ID 在一个案件的全部版本里 SHALL NOT 重复：每一版的前缀不同（「新的版本」），一版里每个条目的类别和键不同
- 一版的条目 SHALL 按上表的先后排列；行情按股票代码，新闻按发布时刻、再按新闻 ID，公告按受理时刻、再按 accession number，财报发布按发布日，事件按检测时刻、再按事件 ID 排列
- 信号和无信号引用的条目、任务请求和 Task 的输入写的就是条目 ID（signal-format spec、task-dispatch spec 的"状态库条目 ID"）

#### Scenario: 案件股票的条目

- **WHEN** 生成 K-1 的第 1 版
- **THEN** 依次有 15 个条目：s1/account、s1/market、s1/pool、s1/quote/BBB、s1/quote/CCC、s1/quote/QQQ、s1/quote/SPY、s1/quote/XLV、s1/news/102、s1/news/103、s1/filing/0000000002-26-000002、s1/filing/0000000002-26-000003、s1/earnings/BBB:2026Q3、s1/earnings/BBB:2026Q4、s1/surprises/BBB

#### Scenario: 事件的条目排在 surprise 之后

- **GIVEN** 状态库里有 BBB 的事件 BBB:2026-10-14:price_down 和 BBB:2026-10-14:news，检测时刻都是 2026-10-14 14:00Z
- **WHEN** 生成 K-1 的第 1 版
- **THEN** 依次有 17 个条目：上一个场景的 15 个，然后是 s1/event/BBB:2026-10-14:news、s1/event/BBB:2026-10-14:price_down

#### Scenario: 寻股的条目

- **WHEN** 生成 K-2 的第 1 版
- **THEN** 依次有 7 个条目：s1/account、s1/market、s1/pool、s1/quote/CCC、s1/quote/QQQ、s1/quote/SPY、s1/screen

#### Scenario: 复盘的条目

- **WHEN** 生成 K-3 的第 1 版
- **THEN** 依次有 6 个条目：s1/account、s1/market、s1/pool、s1/quote/CCC、s1/quote/QQQ、s1/quote/SPY

### Requirement: 案件股票的事实

针对单只股票的案件 SHALL 另有案件股票的下面这些事实：

| 条目 | 内容 |
| --- | --- |
| 行情 | 案件股票和它的板块 ETF 各一个（和共享事实的行情一起取）；板块取股票池记录里的板块 |
| 新闻 | 状态库里标注了这只股票、发布时刻不早于新闻的回看起点的新闻，一条一个条目，内容原样复制（news-and-filings spec「新闻和公告的条目」） |
| 公告 | 状态库里标注了这只股票、受理时刻不早于公告的回看起点的公告，一份一个条目，内容原样复制 |
| 财报发布 | 状态库里这只股票每次发布的最新版本（company-data spec「读取财报」），一次一个条目，内容原样复制 |
| surprise | 状态库里这只股票最近一次保存的 surprise，按报告期从新到旧；没有刷新过时没有这个条目；刷新过、数据源没有 surprise 时有这个条目，里面没有季度 |
| 事件 | 状态库里这只股票检测时刻不早于新闻的回看起点的事件（event-detection spec「读取事件」），一个一个条目，内容原样复制（event-rules spec「事件的内容」）；包括没有排队的、交给紧急风险退出的 |

回看起点：从生成时刻归属的交易日往前数「回看的交易日数」个交易日，那一天的收盘时刻；新闻和公告各有自己的交易日数（「快照配置」），事件用新闻的。一个交易日从上一个交易日收盘到这个交易日收盘，所以回看的是包括当前交易日在内的最近这么多个交易日。

- 只读状态库里已经存下的：MUST NOT 向新闻源、公告源和公司数据的数据源请求
- 案件股票不在股票池里时 SHALL 报错，不生成快照

#### Scenario: 回看起点的边界

- **WHEN** 生成 K-1 的第 1 版
- **THEN** 新闻的回看起点是 2026-10-07 20:00Z：有 N2、N3，没有 N1
- **AND** 公告的回看起点是 2026-07-14 20:00Z：有 F2、F3，没有 F1
- **AND** 新闻源、公告源和公司数据的数据源都没有收到请求

#### Scenario: 收盘以后的回看起点

- **WHEN** 当前时刻 2026-10-14 21:00Z，生成 K-1 的第 1 版
- **THEN** 新闻的回看起点是 2026-10-08 20:00Z：只有 N3

#### Scenario: 内容原样复制

- **WHEN** 生成 K-1 的第 1 版
- **THEN** s1/news/103 的标题是"BBB wins hospital contract"，正文是 `<p>BBB 中标</p>`，发布时刻 2026-10-14 12:00:00Z，其余各项和状态库里的 N3 相同
- **AND** s1/filing/0000000002-26-000003 是 8-K，item 依次是 2.02、9.01，受理时刻 2026-10-13 20:30:00Z
- **AND** s1/earnings/BBB:2026Q3：发布日 2026-10-13，盘后，EPS 预期 1.10、实际 1.18，营收预期 2,000,000,000、实际 2,050,000,000
- **AND** s1/earnings/BBB:2026Q4：发布日 2027-01-26，没有发布时段，EPS 预期 1.25，营收预期 2,100,000,000，没有实际值
- **AND** s1/surprises/BBB 有一个季度：报告期 2026-09-30，2026 财年第 3 季度，EPS 实际 1.18、预期 1.10，surprise 0.08，surprise 百分比 7.2727

#### Scenario: 事件的回看起点和内容

- **GIVEN** 状态库里 BBB 的事件：BBB:2026-10-07:news（检测时刻 2026-10-07 19:59:59Z）；BBB:2026-10-08:earnings（检测时刻 2026-10-07 20:00:00Z，来源财报日历，引用 BBB:2026Q3）；BBB:2026-10-14:price_down（检测时刻 2026-10-14 14:00Z，强度 -0.052，处理是排队）；CCC:2026-10-14:news
- **WHEN** 生成 K-1 的第 1 版
- **THEN** 事件的条目依次是 s1/event/BBB:2026-10-08:earnings、s1/event/BBB:2026-10-14:price_down；没有 BBB:2026-10-07:news 和 CCC 的事件
- **AND** s1/event/BBB:2026-10-14:price_down 的强度正好是 -0.052，其余各项和状态库里的相同

#### Scenario: 没有刷新过财报

- **GIVEN** 状态库里没有 BBB 的财报日历和 surprise
- **WHEN** 生成 K-1 的第 1 版
- **THEN** 没有 earnings 和 surprises 的条目，其余照常

#### Scenario: 数据源没有 surprise

- **GIVEN** 刷新过 BBB 的财报，数据源没有它的 surprise
- **WHEN** 生成 K-1 的第 1 版
- **THEN** 有 s1/surprises/BBB，里面没有季度

#### Scenario: 没有新闻和公告

- **GIVEN** 状态库里没有标注 BBB 的新闻和公告，也没有 BBB 的事件
- **WHEN** 生成 K-1 的第 1 版
- **THEN** 没有 news、filing 和 event 的条目，其余照常

#### Scenario: 案件股票不在股票池里

- **WHEN** 生成 ZZZ 的关注股事件的第 1 版，ZZZ 不在股票池里
- **THEN** 报错，没有这个案件的快照
