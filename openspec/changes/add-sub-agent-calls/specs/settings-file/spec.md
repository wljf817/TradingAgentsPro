## MODIFIED Requirements

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
