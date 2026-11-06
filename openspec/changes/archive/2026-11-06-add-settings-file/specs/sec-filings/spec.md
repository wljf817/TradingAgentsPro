# Spec Delta

## MODIFIED Requirements

### Requirement: SEC 配置

适配器 SHALL 可以配置：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| User-Agent | 写明联系方式，由人填写；部署时写在配置文件的 `sec.user_agent`（settings-file spec） | 无，必须给出 |
| 网站地址 | 代码表和公告原文的地址 | `https://www.sec.gov` |
| 数据地址 | 公司公告列表的地址 | `https://data.sec.gov` |
| 超时时长 | 每次请求等多久没有响应就算超时 | 10 秒 |
| 每秒最多请求数 | 见「User-Agent 和请求间隔」；SEC 的上限是每秒 10 次 | 8 |

User-Agent 和两个地址 MUST NOT 为空，超时时长 MUST 大于 0，每秒最多请求数 MUST 在 1 到 10 之间，否则配置报错。

#### Scenario: SEC 配置非法

- **WHEN** User-Agent 为空字符串；或者网站地址为空字符串；或者超时时长为 0 秒；或者每秒最多请求数为 0 或 11
- **THEN** 配置报错
