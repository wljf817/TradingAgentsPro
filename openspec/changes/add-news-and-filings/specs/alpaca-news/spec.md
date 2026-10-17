# Spec Delta

## Purpose

Alpaca 新闻源通过 Alpaca 市场数据 API 的新闻接口（Benzinga 稿件）实现新闻和公告模块的新闻源：按股票和起始时刻取完全部分页，把字段映射成新闻的条目，并把调用失败分成数据源不可用和原样报错两类。

## ADDED Requirements

### Requirement: 请求和分页

适配器 SHALL 用一次查询取一组股票的新闻：向 Alpaca 的新闻接口 `/v1beta1/news` 发 GET 请求，参数是：

| 参数 | 值 |
| --- | --- |
| symbols | 调用方给出的股票，用逗号连接 |
| start | 起始时刻舍去秒以下的部分，UTC，RFC 3339；多取到的由「起始时刻之前发布的新闻」去掉 |
| include_content | true：带上正文 |
| limit | 50：每页最多 50 条，是 Alpaca 允许的上限 |
| sort | asc：按时间从早到晚 |

每个请求 SHALL 带上 API key ID 和 API secret。回复里有下一页的标记（next_page_token）时，SHALL 带着这个标记（page_token）和同样的参数再请求，直到没有下一页；全部页的新闻合在一起返回。

本 spec 的场景除非另有说明，都使用下面的基准数据（Alpaca 新闻接口回复里的一条新闻）：

| 字段 | 新闻 101 | 新闻 102 |
| --- | --- | --- |
| id | 101 | 102 |
| headline | AAA raises guidance | AAA, BBB to merge |
| summary | AAA lifted its full-year outlook | 空字符串 |
| content | `<p>AAA 上调全年指引</p>` | 空字符串 |
| author | Benzinga Newsdesk | 空字符串 |
| source | benzinga | benzinga |
| url | https://www.benzinga.com/101 | null |
| symbols | ["AAA"] | ["AAA", "BBB"] |
| created_at | 2026-10-14T12:00:05Z | 2026-10-14T13:30:00Z |
| updated_at | 2026-10-14T12:30:00Z | 2026-10-14T13:30:00Z |
| images | 两张图 | 空 |

#### Scenario: 一页

- **GIVEN** Alpaca 回复新闻 101、102，没有下一页
- **WHEN** 取 AAA、BBB 自 2026-10-14T12:00:00Z 的新闻
- **THEN** Alpaca 收到一个请求：symbols 是 AAA,BBB，start 是 2026-10-14T12:00:00Z，include_content 是 true，limit 50，sort asc
- **AND** 结果是新闻 101、102

#### Scenario: 三页

- **GIVEN** 第一页回复新闻 101，下一页标记 P2；带 P2 的请求回复新闻 102，下一页标记 P3；带 P3 的请求回复新闻 103，没有下一页
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 的新闻
- **THEN** Alpaca 依次收到三个请求，第二个带 page_token P2，第三个带 page_token P3，其他参数和第一个相同
- **AND** 结果是新闻 101、102、103

#### Scenario: 请求带上认证

- **GIVEN** API key ID 是 K1，API secret 是 S1
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 的新闻
- **THEN** Alpaca 收到的每个请求都带有 API key ID K1 和 API secret S1

### Requirement: 字段的映射

适配器 SHALL 按下表把 Alpaca 的一条新闻映射为新闻的条目（news-and-filings spec「新闻和公告的条目」）：

| 新闻的条目 | Alpaca |
| --- | --- |
| 新闻 ID | id |
| 标题 | headline |
| 摘要 | summary |
| 正文 | content，原样 |
| 作者 | author |
| 稿件来源 | source |
| 链接 | url；为 null 时没有链接 |
| 标注的股票 | symbols，顺序不变 |
| 发布时刻 | created_at |

updated_at 和 images 不用。

#### Scenario: 有正文和链接的新闻

- **GIVEN** Alpaca 回复新闻 101
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 的新闻
- **THEN** 新闻 ID 101，标题 AAA raises guidance，摘要 AAA lifted its full-year outlook，正文 `<p>AAA 上调全年指引</p>`，作者 Benzinga Newsdesk，稿件来源 benzinga，链接 https://www.benzinga.com/101，标注的股票 AAA，发布时刻 2026-10-14 12:00:05Z

#### Scenario: 没有正文和链接的新闻

- **GIVEN** Alpaca 回复新闻 102
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 的新闻
- **THEN** 摘要、正文、作者都为空，没有链接，标注的股票依次是 AAA、BBB

### Requirement: 起始时刻之前发布的新闻

Alpaca 回复里发布时刻（created_at）早于起始时刻的新闻，适配器 SHALL 从结果里去掉；发布时刻正好等于起始时刻的保留。

#### Scenario: 早于起始时刻的去掉

- **GIVEN** Alpaca 回复新闻 100（created_at 2026-10-14T11:59:59Z）和新闻 101
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 的新闻
- **THEN** 结果只有新闻 101

#### Scenario: 正好等于起始时刻

- **GIVEN** Alpaca 回复新闻 101
- **WHEN** 取 AAA 自 2026-10-14T12:00:05Z 的新闻
- **THEN** 结果有新闻 101

#### Scenario: 起始时刻带秒以下的部分

- **GIVEN** Alpaca 回复新闻 101（created_at 2026-10-14T12:00:05Z）
- **WHEN** 取 AAA 自 2026-10-14T12:00:05.300Z 的新闻
- **THEN** Alpaca 收到的 start 是 2026-10-14T12:00:05Z
- **AND** 结果里没有新闻 101

### Requirement: 新闻请求失败的分类

适配器 SHALL 按下表处理调用 Alpaca 的失败：

| 情况 | 结果 |
| --- | --- |
| 超时、连不上、连接中断；Alpaca 返回 429（限频）或 5xx | 数据源不可用，带上请求和状态码或错误 |
| Alpaca 返回其他 4xx（比如 401、403、400） | 原样报错：是 key、权限或程序的问题，不当作数据源不可用 |
| 回复的内容不是约定的格式 | 原样报错 |

分页途中任何一页失败，整次调用按这一页的失败处理，前面几页取到的新闻不返回。适配器 MUST NOT 自己重试。

#### Scenario: 超时

- **WHEN** 取 AAA 的新闻，Alpaca 超过超时时长没有响应
- **THEN** 报数据源不可用

#### Scenario: 连不上

- **WHEN** 取 AAA 的新闻，连不上 Alpaca
- **THEN** 报数据源不可用

#### Scenario: 限频

- **WHEN** 取 AAA 的新闻，Alpaca 返回 429
- **THEN** 报数据源不可用，Alpaca 只收到一个请求

#### Scenario: 服务端错误

- **WHEN** 取 AAA 的新闻，Alpaca 返回 503
- **THEN** 报数据源不可用

#### Scenario: key 不对

- **WHEN** 取 AAA 的新闻，Alpaca 返回 401
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 参数不合规

- **WHEN** 取 AAA 的新闻，Alpaca 返回 400
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 回复缺字段

- **WHEN** 取 AAA 的新闻，Alpaca 回复的新闻缺了 headline
- **THEN** 原样报错，不报数据源不可用

#### Scenario: 第二页失败

- **GIVEN** 第一页回复新闻 101，下一页标记 P2；带 P2 的请求返回 503
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 的新闻
- **THEN** 报数据源不可用，不返回新闻 101

### Requirement: Alpaca 新闻配置

适配器 SHALL 可以配置：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| 地址 | Alpaca 市场数据 API 的地址 | `https://data.alpaca.markets` |
| 超时时长 | 每次请求等多久没有响应就算超时 | 10 秒 |
| API key ID、API secret | 每次请求都带上，用于认证；和模拟盘用同一组 | 无，必须给出 |

超时时长 MUST 大于 0，否则配置报错。

#### Scenario: 超时时长非法

- **WHEN** 超时时长为 0 秒
- **THEN** 配置报错
