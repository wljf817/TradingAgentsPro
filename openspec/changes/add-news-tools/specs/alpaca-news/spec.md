## MODIFIED Requirements

### Requirement: 请求和分页

适配器 SHALL 用一次查询取一组股票的新闻：向 Alpaca 的新闻接口 `/v1beta1/news` 发 GET 请求，参数是：

| 参数 | 值 |
| --- | --- |
| symbols | 调用方给出的股票，用逗号连接 |
| start | 起始时刻舍去秒以下的部分，UTC，RFC 3339；多取到的由「起始时刻之前发布的新闻」去掉 |
| include_content | true：带上正文 |
| limit | 50：每页最多 50 条，是 Alpaca 允许的上限 |
| sort | asc：按时间从早到晚 |
| end | 只有调用方给了结束时刻才带：结束时刻舍去秒以下的部分再加 1 秒，UTC，RFC 3339；Alpaca 的 end 包含这一刻，多取到的由「结束时刻之后发布的新闻」去掉 |

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

#### Scenario: 带结束时刻

- **GIVEN** Alpaca 回复新闻 101，没有下一页
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 到 2026-10-14T13:00:00Z 的新闻
- **THEN** Alpaca 收到的 end 是 2026-10-14T13:00:01Z，其他参数和没有结束时刻时相同
- **AND** 结果是新闻 101

## ADDED Requirements

### Requirement: 结束时刻之后发布的新闻

调用方给了结束时刻时，Alpaca 回复里发布时刻（created_at）晚于结束时刻的新闻，适配器 SHALL 从结果里去掉；发布时刻正好等于结束时刻的保留。

#### Scenario: 晚于结束时刻的去掉

- **GIVEN** Alpaca 回复新闻 101（created_at 2026-10-14T12:00:05Z）
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 到 2026-10-14T12:00:04.500Z 的新闻
- **THEN** Alpaca 收到的 end 是 2026-10-14T12:00:05Z
- **AND** 结果里没有新闻 101

#### Scenario: 正好等于结束时刻

- **GIVEN** Alpaca 回复新闻 101
- **WHEN** 取 AAA 自 2026-10-14T12:00:00Z 到 2026-10-14T12:00:05Z 的新闻
- **THEN** 结果有新闻 101
