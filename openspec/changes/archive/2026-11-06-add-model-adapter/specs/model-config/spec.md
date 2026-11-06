# Spec Delta

## MODIFIED Requirements

### Requirement: 模型文件

程序 SHALL 从模型文件读取模型的配置和预算的额度。模型文件是工作目录下的 TOML 文件 `config/models.toml`，里面有厂商的 API key，MUST NOT 进 git。

- 模型文件在启动时读一次，运行中不变；改了文件要重启才生效
- 模型文件不存在时，程序 SHALL 在启动时从模板生成它，权限是只有所有者能读写，然后照常启动。模板进 git，里面全是替身，没有 key
- 模型文件已经存在时 SHALL NOT 改动它
- 每个部署有自己的模型文件：换模型、地址或 key 就是改这个文件，重启生效，不用提交代码

#### Scenario: 运行中改文件

- **GIVEN** 主程序在运行
- **WHEN** 改了模型文件
- **THEN** 运行中的主程序仍用启动时读到的模型；重启以后用新的模型

#### Scenario: 第一次启动时生成

- **GIVEN** 模型文件不存在
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 程序生成模型文件，内容就是模板，只有所有者能读写，日志写一条 INFO
- **AND** 六个 agent 都用替身，启动完成

#### Scenario: 已有的文件不改动

- **GIVEN** 模型文件已经存在，内容和模板不同
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 模型文件不变，程序用它

#### Scenario: 仓库里的模型文件

- **WHEN** 读仓库里的模板
- **THEN** 配置正确，六个 agent 都是替身的模型，用到的模型都有单价，没有 key

#### Scenario: 不进 git

- **WHEN** 检查 `config/models.toml` 是否被 git 忽略
- **THEN** 被忽略

### Requirement: 文件的内容

模型文件 SHALL 写下表的内容：

| 位置 | 项 | 要求 |
| --- | --- | --- |
| `budgets` | `general`、`reserve`、`exploration` | 普通预算、持仓保留预算、寻股预算的额度，每个交易日多少美元；三项都要写，要求见 call-budget spec「预算的配置」 |
| `agents.<agent>` | `primary` | 这个 agent 的主模型；`<agent>` 是 `pm`、`news`、`quant`、`fundamentals`、`critic`、`reviewer`，六个都要写 |
| `agents.<agent>` | `fallbacks` | 这个 agent 的备用模型，按顺序；不写时没有备用模型 |
| `vendors.<厂商>` | `interface` | 这家厂商的接口：`anthropic`、`openai` 或 `standin`（替身），含义见 model-adapter spec「两种接口」「按厂商分派」 |
| `vendors.<厂商>` | `base_url` | 接口的地址；接口是 `anthropic`、`openai` 时必须写，`standin` 时不能写 |
| `vendors.<厂商>` | `api_key` | 这家厂商的 API key；接口是 `anthropic`、`openai` 时必须写，不能为空，`standin` 时不能写 |
| `prices."<模型>"` | `input`、`output` | 这个模型的输入单价和输出单价，美元/百万 token；两项都要写，要求见 call-budget spec「模型的单价」 |

- 模型写作"厂商/模型名"：第一个 `/` 前面是厂商，后面是模型名，要求见 model-fallback spec「模型的配置」
- 额度和单价 SHALL 按文件里写的数精确读取
- 任何 agent 用到的模型，它的厂商都 SHALL 写在 `vendors` 里；没有 agent 用到的厂商可以写，不报错
- 出现上表以外的表或项 MUST 配置报错
- 读到的内容 SHALL 通过 model-fallback spec「模型的配置」「厂商校验」和 call-budget spec「模型的单价」「预算的配置」的校验，不通过时 MUST 配置报错

本 spec 的场景用下面的基准文件，模型的简写同 model-fallback spec，比如 p-1 就是 anthropic/p-1：

```toml
[budgets]
general = 20.00
reserve = 10.00
exploration = 5.00

[vendors.anthropic]
interface = "anthropic"
base_url = "https://api.anthropic.example"
api_key = "K-1"

[vendors.google]
interface = "openai"
base_url = "https://api.google.example/v1"
api_key = "K-2"

[vendors.openai]
interface = "openai"
base_url = "https://api.openai.example/v1"
api_key = "K-3"

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
- **AND** anthropic 的接口是 `anthropic`，地址是 `https://api.anthropic.example`，key 是 K-1

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

- **WHEN** 不写 `budgets`；或者不写 `budgets.reserve`；或者不写 `agents.reviewer`；或者 Critic 不写 `primary`；或者 p-1 不写 `output`；或者不写 `vendors.openai`
- **THEN** 都配置报错

#### Scenario: 多写了内容

- **WHEN** `budgets` 多写一项 `daily`；或者 `agents` 多写一个 `trader`；或者 Critic 多写一项 `backup`；或者 p-1 多写一项 `cached`；或者 anthropic 多写一项 `region`；或者多写一个表 `limits`
- **THEN** 都配置报错

#### Scenario: 不通过校验

- **WHEN** `budgets.general` 写成 0；或者 Critic 的主模型写成 anthropic/c-1；或者 PM 的备用模型重复写 p-2；或者不写 s-1 的单价；或者 p-1 的输入单价写成 -0.01
- **THEN** 都配置报错

#### Scenario: 厂商写错

- **WHEN** anthropic 的 `interface` 写成 `gemini`；或者 anthropic 不写 `base_url`；或者 anthropic 不写 `api_key`；或者 anthropic 的 `api_key` 是空的；或者替身的厂商写了 `base_url`；或者替身的厂商写了 `api_key`
- **THEN** 都配置报错

#### Scenario: 没有 agent 用到的厂商

- **WHEN** `vendors` 多写一家 acme，没有 agent 用它的模型
- **THEN** 配置正确

### Requirement: 文件出错

模型文件不存在又生成不了、不是合法的 TOML，或者内容不符合「文件的内容」时，SHALL 算配置报错；bootstrap 启动时 SHALL 拒绝启动，见 bootstrap spec「启动失败」。

- 错误信息 SHALL 写明出错的原因，MUST NOT 含有 `api_key` 的值

#### Scenario: 文件不存在

- **GIVEN** 模型文件不存在，它的目录不能写
- **WHEN** 启动时生成模型文件
- **THEN** 配置报错，错误信息里有文件的路径

#### Scenario: 不是合法的 TOML

- **WHEN** 模型文件的内容是 `[budgets`
- **THEN** 配置报错，错误信息写明解析失败

#### Scenario: 内容不合要求

- **WHEN** 基准文件不写 `budgets.reserve`
- **THEN** 配置报错，错误信息里有 `reserve`

#### Scenario: 错误信息里没有 key

- **WHEN** 基准文件的 anthropic 多写一项 `region`；或者 anthropic 不写 `base_url`
- **THEN** 配置报错，错误信息里没有 K-1
