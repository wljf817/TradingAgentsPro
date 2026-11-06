# Spec Delta

## MODIFIED Requirements

### Requirement: 读到以后怎么用

Agent 网关的模型配置和单价 SHALL 是模型文件里的；网关配置的三个预算额度 SHALL 是模型文件里的，网关配置的其他项 SHALL 来自配置文件（settings-file spec）。

#### Scenario: 额度来自文件

- **GIVEN** 模型文件的 `general` 写成 3.00，装配好的网关
- **WHEN** 今天记到普通预算的费用是 2.99，取得预算的情况
- **THEN** 普通预算没用完
- **WHEN** 接着再记下 0.01，取得预算的情况
- **THEN** 普通预算已经用完

#### Scenario: 其他项用默认值

- **GIVEN** 配置文件没有写 `gateway` 一节
- **WHEN** 用基准文件装配网关
- **THEN** 网关配置除了三个预算额度，都是默认值

#### Scenario: 其他项来自配置文件

- **GIVEN** 配置文件的 `gateway.max_pm_rounds` 写成 3
- **WHEN** 用基准文件装配网关
- **THEN** 每个案件 PM 最多派 3 轮，三个预算额度依次是 20、10、5
