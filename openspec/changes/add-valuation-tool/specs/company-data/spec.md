## MODIFIED Requirements

### Requirement: 公司数据配置

公司数据模块的配置项 SHALL 可配置，默认值如下：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| 窗口往前天数 | 30 | 刷新财报日历时，取当前 UTC 日期往前这么多天以来的发布 |
| 窗口往后天数 | 90 | 刷新财报日历时，取到当前 UTC 日期往后这么多天为止的发布 |
| `financial_quarters` | 8 | `financials` 最多列多少个季度、多少次财报发布（financials-tool spec） |
| `valuation_years` | 5 | `valuation` 的历史位置往前看多少年（valuation-tool spec「历史位置」） |
| `max_figures_age_days` | 200 | `valuation` 的数字最近一期结束超过这么多天就不用（valuation-tool spec「太旧的数字」） |

五项都 SHALL 是大于 0 的整数，否则配置报错。

#### Scenario: 默认配置

- **WHEN** 不传任何配置项新建配置
- **THEN** 窗口往前 30 天，往后 90 天

#### Scenario: 改窗口

- **GIVEN** 基准数据，窗口往前 10 天、往后 60 天
- **WHEN** 刷新 AAA
- **THEN** 财报日历的请求是发布日在 2026-10-04 到 2026-12-13 之间

#### Scenario: 配置非法

- **WHEN** 窗口往前天数为 0，或者窗口往后天数为 -1，或者 `financial_quarters`、`valuation_years`、`max_figures_age_days` 为 0
- **THEN** 配置报错
