## ADDED Requirements

### Requirement: 工具的说明

`list_news` 和 `read_news` 交给模型的说明 SHALL 写明股票和时刻由程序给出，参数里没有股票代码。

#### Scenario: 说明写明股票由程序给出

- **WHEN** 调用 News 运行 T-2
- **THEN** 请求里 `list_news` 和 `read_news` 的说明都写明股票和时刻由程序给出，参数里没有股票代码
