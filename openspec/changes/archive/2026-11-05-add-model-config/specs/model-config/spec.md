# Spec Delta

## Purpose

规定模型文件放在哪里、写什么、什么时候读、出错时怎么办：六个 agent 的模型、模型的单价和三个预算的额度都写在这个文件里，换模型不用改代码。

## ADDED Requirements

### Requirement: 模型文件

程序 SHALL 从模型文件读取模型的配置和预算的额度。模型文件是仓库里的 TOML 文件 `src/tradingagentspro/bootstrap/models.toml`，跟着代码一起部署，不放密钥。

- 模型文件在启动时读一次，运行中不变；改了文件要重启才生效
- 换模型就是改这个文件，再按 deployment spec「更新」部署

#### Scenario: 运行中改文件

- **GIVEN** 主程序在运行
- **WHEN** 改了模型文件
- **THEN** 运行中的主程序仍用启动时读到的模型；重启以后用新的模型

#### Scenario: 仓库里的模型文件

- **WHEN** 读仓库里的模型文件
- **THEN** 配置正确，六个 agent 都有模型，用到的模型都有单价

### Requirement: 文件的内容

模型文件 SHALL 写下表的内容：

| 位置 | 项 | 要求 |
| --- | --- | --- |
| `budgets` | `general`、`reserve`、`exploration` | 普通预算、持仓保留预算、寻股预算的额度，每个交易日多少美元；三项都要写，要求见 call-budget spec「预算的配置」 |
| `agents.<agent>` | `primary` | 这个 agent 的主模型；`<agent>` 是 `pm`、`news`、`quant`、`fundamentals`、`critic`、`reviewer`，六个都要写 |
| `agents.<agent>` | `fallbacks` | 这个 agent 的备用模型，按顺序；不写时没有备用模型 |
| `prices."<模型>"` | `input`、`output` | 这个模型的输入单价和输出单价，美元/百万 token；两项都要写，要求见 call-budget spec「模型的单价」 |

- 模型写作"厂商/模型名"：第一个 `/` 前面是厂商，后面是模型名，要求见 model-fallback spec「模型的配置」
- 额度和单价 SHALL 按文件里写的数精确读取
- 出现上表以外的表或项 MUST 配置报错
- 读到的内容 SHALL 通过 model-fallback spec「模型的配置」「厂商校验」和 call-budget spec「模型的单价」「预算的配置」的校验，不通过时 MUST 配置报错

本 spec 的场景用下面的基准文件，模型的简写同 model-fallback spec，比如 p-1 就是 anthropic/p-1：

```toml
[budgets]
general = 20.00
reserve = 10.00
exploration = 5.00

[agents.pm]
primary = "anthropic/p-1"
fallbacks = ["anthropic/p-2", "google/p-3"]

[agents.news]
primary = "google/s-1"

[agents.quant]
primary = "google/s-1"

[agents.fundamentals]
primary = "google/s-1"

[agents.critic]
primary = "openai/c-1"

[agents.reviewer]
primary = "anthropic/p-1"

[prices]
"anthropic/p-1" = { input = 2.00, output = 10.00 }
"anthropic/p-2" = { input = 1.00, output = 5.00 }
"google/p-3" = { input = 0.15, output = 0.60 }
"google/s-1" = { input = 0.15, output = 0.60 }
"openai/c-1" = { input = 1.00, output = 3.00 }
```

#### Scenario: 基准文件

- **WHEN** 读基准文件
- **THEN** 配置正确；PM 的模型依次是 p-1、p-2、p-3；Reviewer 没有备用模型，主模型和 PM 的主模型是同一个模型
- **AND** p-1 的输入单价 2、输出单价 10
- **AND** 三个预算的额度依次是 20、10、5

#### Scenario: 单价精确读取

- **WHEN** 读基准文件，算 p-3 输入 1,000,000 token、输出 0 的费用
- **THEN** 费用精确等于 0.15

#### Scenario: 模型名里有斜杠

- **WHEN** News 的主模型写成 "google/gemma/s-1"，单价也按这个写
- **THEN** 配置正确，厂商是 google，模型名是 gemma/s-1

#### Scenario: 模型写法不对

- **WHEN** News 的主模型写成 "google"；或者 "/s-1"；或者 "google/"
- **THEN** 都配置报错

#### Scenario: 缺少内容

- **WHEN** 不写 `budgets`；或者不写 `budgets.reserve`；或者不写 `agents.reviewer`；或者 Critic 不写 `primary`；或者 p-1 不写 `output`
- **THEN** 都配置报错

#### Scenario: 多写了内容

- **WHEN** `budgets` 多写一项 `daily`；或者 `agents` 多写一个 `trader`；或者 Critic 多写一项 `backup`；或者 p-1 多写一项 `cached`；或者多写一个表 `limits`
- **THEN** 都配置报错

#### Scenario: 不通过校验

- **WHEN** `budgets.general` 写成 0；或者 Critic 的主模型写成 anthropic/c-1；或者 PM 的备用模型重复写 p-2；或者不写 s-1 的单价；或者 p-1 的输入单价写成 -0.01
- **THEN** 都配置报错

### Requirement: 读到以后怎么用

Agent 网关的模型配置和单价 SHALL 是模型文件里的；网关配置的三个预算额度 SHALL 是模型文件里的，网关配置的其他项用默认值。

#### Scenario: 额度来自文件

- **GIVEN** 模型文件的 `general` 写成 3.00，装配好的网关
- **WHEN** 今天记到普通预算的费用是 2.99，取得预算的情况
- **THEN** 普通预算没用完
- **WHEN** 接着再记下 0.01，取得预算的情况
- **THEN** 普通预算已经用完

#### Scenario: 其他项用默认值

- **WHEN** 用基准文件装配网关
- **THEN** 网关配置除了三个预算额度，都是默认值

### Requirement: 文件出错

模型文件不存在、不是合法的 TOML，或者内容不符合「文件的内容」时，SHALL 算配置报错；bootstrap 启动时 SHALL 拒绝启动，见 bootstrap spec「启动失败」。

- 错误信息 SHALL 写明出错的原因

#### Scenario: 文件不存在

- **WHEN** 模型文件不存在
- **THEN** 配置报错，错误信息里有文件的路径

#### Scenario: 不是合法的 TOML

- **WHEN** 模型文件的内容是 `[budgets`
- **THEN** 配置报错，错误信息写明解析失败

#### Scenario: 内容不合要求

- **WHEN** 基准文件不写 `budgets.reserve`
- **THEN** 配置报错，错误信息里有 `reserve`
