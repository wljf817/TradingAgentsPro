# settings-file Specification

## Purpose
规定主程序的配置文件放在哪里、写什么、怎样生成、什么时候读、出错时怎么办：连接地址、密钥和全部运行参数都写在这个文件里，改参数不用改代码。模型、厂商和预算额度在模型文件里，见 model-config spec。

## Requirements

### Requirement: 配置文件

程序 SHALL 从配置文件读取连接信息、密钥和运行参数。配置文件是工作目录下的 TOML 文件 `config/settings.toml`，里面有密钥，MUST NOT 进 git。

- 配置文件在启动时读一次，运行中不变；改了文件要重启才生效
- 配置文件不存在时，程序 SHALL 在启动时生成它，权限是只有所有者能读写，内容见「生成的文件」
- 配置文件已经存在时 SHALL NOT 改动它
- 每个部署有自己的配置文件

本 spec 的场景用下面的基准文件，它只写九个必填项：

```toml
[database]
url = "postgresql://trader:P-1@db.local:5432/trading"

[alpaca]
key_id = "PK-1"
secret_key = "S-1"

[discord]
token = "T-1"
guild_id = 3003
channel_id = 2002
user_id = 1001

[finnhub]
api_key = "F-1"

[sec]
user_agent = "tradingagentspro test@example.com"
```

#### Scenario: 运行中改文件

- **GIVEN** 主程序在运行
- **WHEN** 改了配置文件
- **THEN** 运行中的主程序仍用启动时读到的值；重启以后用新的值

#### Scenario: 第一次启动时生成

- **GIVEN** 配置文件不存在
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 程序生成配置文件，只有所有者能读写，日志写一条 INFO
- **AND** 启动失败，错误日志在一条里列出九个必填项
- **AND** 没有连接数据库、Alpaca、OpenD、Discord

#### Scenario: 已有的文件不改动

- **GIVEN** 配置文件已经存在，内容是基准文件
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 配置文件不变，程序用它

#### Scenario: 不进 git

- **WHEN** 检查 `config/settings.toml` 是否被 git 忽略
- **THEN** 被忽略

### Requirement: 文件的内容

配置文件 SHALL 按下表分节，每节的项就是那份配置的配置项，含义和要求见表里的 spec：

| 节 | 内容 | 含义和要求 |
| --- | --- | --- |
| `database` | 状态库的地址 | 下面的必填项表 |
| `alpaca` | Alpaca 模拟盘 | alpaca-broker spec「Alpaca 配置」 |
| `discord` | Discord 机器人 | discord-notification spec「Discord 配置」、manual-plans spec「提交接口」 |
| `finnhub` | Finnhub | finnhub-company-data spec「Finnhub 配置」 |
| `sec` | SEC EDGAR | sec-filings spec「SEC 配置」 |
| `alpaca_bars` | Alpaca 日线 | alpaca-bars spec「Alpaca 日线配置」 |
| `alpaca_news` | Alpaca 新闻 | alpaca-news spec「Alpaca 新闻配置」 |
| `cboe` | Cboe | market-context spec「Cboe 数据源」 |
| `moomoo` | OpenD 的连接 | moomoo-quotes spec「连接配置」 |
| `moomoo_securities` | moomoo 股票列表 | moomoo-securities spec「moomoo 股票列表配置」 |
| `llm` | 模型调用 | model-adapter spec |
| `standin` | 替身 | stand-ins spec「替身的配置」 |
| `calendar` | 交易日历的加载范围 | bootstrap spec「交易日历的加载范围」 |
| `risk` | 风控 | risk-rules spec「风控阈值配置」 |
| `execution` | 执行模块 | order-lifecycle spec「执行配置」、close-reconciliation spec「收盘对账配置」 |
| `emergency` | 紧急退出 | emergency-exit spec「紧急退出配置」 |
| `health` | 健康监控 | degraded-mode spec「健康配置」 |
| `marketdata` | 行情 | market-data spec「行情配置」、market-context spec |
| `notification` | 人工确认 | confirmation-rules spec「确认规则的配置」 |
| `order_flow` | 下单流程 | order-flow spec「下单流程配置」 |
| `patrol` | 盘中巡检 | session-patrol spec「巡检配置」 |
| `stockpool` | 股票池 | stock-pool spec「股票池配置」 |
| `followups` | 跟进 | follow-ups spec「跟进的配置」 |
| `gateway` | Agent 网关 | signal-format spec「信号的配置」、task-dispatch spec「派发的配置」、model-fallback spec「备用模型的配置」、history-query spec「查询的上限」、sub-agent-calls spec「工具次数」 |
| `scheduler` | 调度器 | case-types spec「研究要求的配置」、case-queue spec「队列配置」、case-stages spec「案件阶段的配置」 |
| `heartbeat` | 复查日和收盘检查 | review-dates spec「复查日与催化剂的配置」、close-check spec「收盘检查的配置」 |
| `close_heartbeat` | 收盘心跳 | close-heartbeat spec「收盘心跳的配置」 |
| `case_flow` | 案件工作流 | case-workflow spec「案件工作流的配置」 |
| `queue_runner` | 排队运行 | queue-runner spec「排队运行的配置」 |
| `snapshot` | 快照 | state-snapshot spec「快照配置」 |
| `events` | 事件规则 | event-rules spec「事件规则的配置」 |
| `event_detection` | 事件检测 | event-detection spec「事件检测的配置」 |
| `companydata` | 公司数据 | company-data spec「公司数据配置」 |
| `screener` | 寻股粗筛 | stock-screen spec「粗筛配置」 |

下面九项是必填项，没有默认值：

| 项 | 含义 | 格式 |
| --- | --- | --- |
| `database.url` | 状态库的地址，含用户名和密码；驱动一律用 psycopg，写不写 `+psycopg` 都可以 | `postgresql://用户:密码@主机:端口/数据库` |
| `alpaca.key_id` | Alpaca 模拟盘的 key ID | 文本 |
| `alpaca.secret_key` | Alpaca 模拟盘的 secret | 文本 |
| `discord.token` | Discord 机器人的 token | 文本 |
| `discord.guild_id` | 服务器 ID | 正整数 |
| `discord.channel_id` | 告警和确认的频道 ID | 正整数 |
| `discord.user_id` | 唯一可以操作的用户 ID | 正整数 |
| `finnhub.api_key` | Finnhub 的 API key | 文本 |
| `sec.user_agent` | 访问 SEC EDGAR 时的 User-Agent，写明联系方式 | 文本 |

- 其余的项都有默认值，默认值写在代码里；文件里没有写的项和没有写的节 SHALL 用默认值
- 时长 SHALL 写秒数；比例和金额 SHALL 按文件里写的数精确读取；日期写 TOML 的日期；集合写数组；枚举写它的值
- Alpaca 的日线和新闻 SHALL 用 `alpaca` 一节的 key

下面这些项 SHALL NOT 写在配置文件里：

| 项 | 在哪里设定 |
| --- | --- |
| `gateway.general_budget`、`gateway.reserve_budget`、`gateway.exploration_budget` | 模型文件的 `budgets`（model-config spec） |
| `alpaca_bars` 和 `alpaca_news` 的 `key_id`、`secret_key` | `alpaca` 一节 |
| `execution.stagger_buys_and_stops` | 由券商决定，装配时固定（bootstrap spec「模拟盘的装配」） |

#### Scenario: 基准文件

- **WHEN** 读基准文件
- **THEN** 九个必填项都读到了，数据库地址的驱动是 psycopg
- **AND** 其余各项都是默认值

#### Scenario: 改一项

- **GIVEN** 基准文件加上 `[risk]` 和 `max_positions = 5`
- **WHEN** 读文件
- **THEN** 风控配置的持仓只数上限是 5，其余各项都是默认值

#### Scenario: 时长写秒数

- **GIVEN** 基准文件加上 `[patrol]` 和 `interval = 30`
- **WHEN** 读文件
- **THEN** 巡检间隔是 30 秒

#### Scenario: 比例精确读取

- **GIVEN** 基准文件加上 `[risk]` 和 `max_trade_risk_ratio = 0.07`
- **WHEN** 读文件
- **THEN** 这个比例精确等于 0.07

#### Scenario: 日期和数组

- **GIVEN** 基准文件加上 `[calendar]` 和 `first_day = 2026-01-01`，以及 `[snapshot]` 和 `indexes = ["SPY"]`
- **WHEN** 读文件
- **THEN** 日历的起点是 2026-01-01，快照的指数只有 SPY

#### Scenario: Alpaca 的日线和新闻用同一组 key

- **WHEN** 读基准文件
- **THEN** Alpaca 的模拟盘、日线和新闻的 key 都是 PK-1 和 S-1

#### Scenario: 写了别处设定的项

- **WHEN** 基准文件加上 `[gateway]` 和 `general_budget = 3`；或者 `[alpaca_news]` 和 `key_id = "X"`；或者 `[execution]` 和 `stagger_buys_and_stops = false`
- **THEN** 都配置报错，错误信息里有这一项

### Requirement: 生成的文件

生成的配置文件 SHALL 由代码按配置项生成，不另外维护一份模板或默认值：

- 文件开头写明怎样用这个文件
- 每节一个表头，节的说明和项的说明来自配置项在代码里的说明
- 必填项 SHALL 写成空字符串，不注释
- 其余的项 SHALL 写成注释，值是生成时代码的默认值：去掉行首的 `# ` 才生效，没有去掉的项继续用代码的默认值
- 生成的文件里 MUST NOT 有任何密钥的值

#### Scenario: 填好必填项

- **GIVEN** 生成的文件，只填了九个必填项
- **WHEN** 读文件
- **THEN** 其余各项都是默认值

#### Scenario: 去掉全部注释

- **GIVEN** 生成的文件，填了九个必填项，全部写成注释的项都去掉了注释
- **WHEN** 读文件
- **THEN** 配置正确，每一项都等于默认值

#### Scenario: 代码改了默认值

- **GIVEN** 生成的文件，只填了九个必填项
- **WHEN** 代码把一项的默认值改了，重启
- **THEN** 程序用新的默认值

#### Scenario: 每一项都写了

- **WHEN** 生成配置文件
- **THEN** 除了「文件的内容」里不写在配置文件的项，每一节的每一项都在文件里

### Requirement: 文件出错

配置文件不存在又生成不了、不是合法的 TOML，或者内容不符合「文件的内容」时，SHALL 算配置报错；bootstrap 启动时 SHALL 拒绝启动，见 bootstrap spec「启动失败」。

- 必填项没有写或为空都算缺少。有缺少的项时 SHALL 一次列出全部缺少的项
- 项的值类型不对或超出范围、未知的项、未知的节 SHALL 配置报错，并且 SHALL 一次列出全部出错的项
- 错误信息 MUST NOT 含有任何项的值，日志里也 MUST NOT 出现

#### Scenario: 一次列出全部缺少的项

- **GIVEN** 基准文件没有 `alpaca.secret_key`，`discord.user_id` 是空字符串
- **WHEN** 读文件
- **THEN** 配置报错，错误信息在一条里列出 `alpaca.secret_key` 和 `discord.user_id`

#### Scenario: 缺少两节

- **GIVEN** 基准文件没有 `finnhub` 和 `sec` 两节
- **WHEN** 读文件
- **THEN** 配置报错，错误信息在一条里列出 `finnhub.api_key` 和 `sec.user_agent`

#### Scenario: Discord 的 ID 不是正整数

- **GIVEN** `discord.channel_id` 是 `general`
- **WHEN** 读文件
- **THEN** 配置报错，错误信息里有 `discord.channel_id`，没有 `general`

#### Scenario: 数据库地址写错

- **GIVEN** `database.url` 是 `pw-9f3k@db`（不是数据库地址）
- **WHEN** 读文件
- **THEN** 配置报错，错误信息里有 `database.url`，没有 `pw-9f3k`

#### Scenario: 超出范围

- **GIVEN** 基准文件加上 `[risk]` 和 `max_positions = 0`
- **WHEN** 读文件
- **THEN** 配置报错，错误信息里有 `risk.max_positions`

#### Scenario: 配置项之间不符合要求

- **GIVEN** 基准文件加上 `[heartbeat]` 和 `min_review_sessions = 9`
- **WHEN** 读文件
- **THEN** 配置报错，错误信息里有 `heartbeat`

#### Scenario: 未知的项和节

- **WHEN** 基准文件加上 `[risk]` 和 `max_position = 5`；或者加上 `[risks]`
- **THEN** 都配置报错，错误信息里有写错的名字

#### Scenario: 一次列出全部出错的项

- **GIVEN** 基准文件加上 `[risk]`、`max_positions = 0` 和 `[patrol]`、`interval = 0`
- **WHEN** 读文件
- **THEN** 配置报错，错误信息在一条里列出 `risk.max_positions` 和 `patrol.interval`

#### Scenario: 不是合法的 TOML

- **WHEN** 配置文件的内容是 `[database`
- **THEN** 配置报错，错误信息写明解析失败

#### Scenario: 错误信息里没有密钥

- **WHEN** 基准文件的 `alpaca` 多写一项 `region = "us"`；或者 `discord.guild_id` 是 `T-9`
- **THEN** 配置报错，错误信息和启动失败的日志里没有 S-1、T-1、P-1、T-9

#### Scenario: 文件不存在又生成不了

- **GIVEN** 配置文件不存在，它的目录不能写
- **WHEN** 启动时生成配置文件
- **THEN** 配置报错，错误信息里有文件的路径

### Requirement: 和默认值不同的项写进日志

程序 SHALL 在读完配置文件以后写一条 INFO 日志，列出值和默认值不同的项和它们的值。

- 必填项 MUST NOT 写进这条日志：它们没有默认值，里面有密钥
- 全部项都是默认值时 SHALL NOT 写这条日志

#### Scenario: 改了两项

- **GIVEN** 基准文件加上 `[risk]`、`max_positions = 5` 和 `[patrol]`、`interval = 30`
- **WHEN** 启动
- **THEN** 日志有一条 INFO，写明 `risk.max_positions = 5` 和 `patrol.interval = 30`
- **AND** 没有别的项，没有 S-1、T-1、P-1、F-1

#### Scenario: 写了和默认值相同的值

- **GIVEN** 基准文件加上 `[risk]` 和这一项的默认值
- **WHEN** 启动
- **THEN** 没有这条日志

#### Scenario: 全是默认值

- **WHEN** 用基准文件启动
- **THEN** 没有这条日志
