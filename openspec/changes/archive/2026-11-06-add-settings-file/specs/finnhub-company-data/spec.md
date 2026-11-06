# Spec Delta

## MODIFIED Requirements

### Requirement: Finnhub 配置

Finnhub 的配置项 SHALL 如下：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| API key | 没有，必须给出 | Finnhub 的 API key，写在配置文件的 `finnhub.api_key`（settings-file spec） |
| 网址 | `https://finnhub.io/api/v1` | Finnhub REST API 的地址 |
| 超时 | 10 秒 | 一个请求等多久没有响应就算超时 |

API key 和网址不能为空，超时大于 0，否则配置报错。显示配置时 MUST NOT 显示 API key 的值。

#### Scenario: 默认配置

- **WHEN** 只给出 API key 新建配置
- **THEN** 网址 `https://finnhub.io/api/v1`，超时 10 秒
- **AND** 配置的文字形式里没有 key 的值

#### Scenario: 配置非法

- **WHEN** 不给 API key，或者 API key 为空字符串，或者网址为空字符串，或者超时为 0 秒
- **THEN** 配置报错
