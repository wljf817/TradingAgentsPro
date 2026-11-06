# Design

## Context

- 33 个配置类的默认值在代码里，`bootstrap/app.py` 用 `XxxConfig()` 默认构造，只有 `SnapshotConfig(indexes=...)` 和 `ExecutionConfig(stagger_buys_and_stops=True)` 写了值。这些类都是 Pydantic，大多 `frozen`、`extra="forbid"`，已经有范围校验。
- `bootstrap/settings.py` 在启动第 1 步读 9 个环境变量。systemd 从 `/etc/tradingagentspro/tradingagentspro.env` 读，只有 root 能看。
- 进行中的 `add-model-adapter` 把模型、地址和 key 放进启动时生成、被 git 忽略的 `config/models.toml`，见它的 design D10、D11。这个变更沿用同样的做法。
- 用 Pydantic 2.13 试过：TOML 的整数和 `Decimal` 都能读成 `timedelta`（秒），TOML 的日期读成 `date`，数组读成 `tuple` 和 `frozenset`。`errors(include_input=False, ...)` 给出的信息里没有输入的值。

## 模块边界

- 只改 `bootstrap` 和几个适配器的配置。`bootstrap/settings.py` import 各模块、适配器和工作流公开的配置类，不新增模块之间的依赖。
- 模块之间传的还是各自的配置模型，`Settings` 只在 `bootstrap` 里用。
- 新增 `CalendarConfig`（`integrations/nyse_calendar.py`）和 `DatabaseConfig`（`bootstrap/settings.py`）。

## Goals / Non-Goals

**Goals:**

- 主程序的每个参数只在一个地方设定：有默认值的在代码，要改的在 `config/settings.toml`
- 线上行为不变：什么都不改时用的值和现在一样

**Non-Goals:**

- 校验配置项之间新的关系：只用各配置类已有的校验
- 给配置文件写单独的说明文档：文件里的说明来自代码的注释，含义在各 spec

## 参数清单

分类的标准：改了以后系统照样成立、只是行为不同的是**参数**；改了就违背外部协议、认不出已有的数据，或者本身是规则表的是**固定值**。

### 运行时可调的参数：挪进 `config/settings.toml`

| 节 | 来自 | 项 |
| --- | --- | --- |
| `database` | 环境变量 → 新的 `DatabaseConfig` | `url` |
| `alpaca` | `AlpacaConfig` 和环境变量 | `key_id`、`secret_key`、`base_url`、`timeout` |
| `discord` | `DiscordConfig` 和环境变量 | `token`、`guild_id`、`channel_id`、`user_id`、`connect_timeout`、`send_timeout`、`submit_timeout` |
| `finnhub` | `FinnhubConfig` 和环境变量 | `api_key`、`base_url`、`timeout` |
| `sec` | `SecConfig` 和环境变量；模块常量 `SEC_URL`、`SEC_DATA_URL` | `user_agent`、`base_url`、`data_url`、`timeout`、`max_requests_per_second` |
| `alpaca_bars` | `AlpacaBarsConfig` | `base_url`、`timeout` |
| `alpaca_news` | `AlpacaNewsConfig` | `base_url`、`timeout` |
| `cboe` | `CboeConfig`；模块常量 `CBOE_VIX_URL` | `url`、`timeout` |
| `moomoo` | `MoomooConfig` | `host`、`port`、`timeout` |
| `moomoo_securities` | `MoomooSecuritiesConfig` | `request_interval` |
| `llm` | `LlmConfig` | `max_tokens` |
| `standin` | `StandInConfig` | `signal_horizon`、`signal_score`、`signal_confidence`、`position_ratio`、`limit_markup`、`stop_ratio`、`emergency_threshold`、`target_ratio`、`entry_markup`、`review_sessions`、`catalyst_sessions`、`exploration_additions` |
| `calendar` | `BootstrapConfig` 的两项 → 新的 `CalendarConfig` | `first_day`、`lookahead` |
| `risk` | `RiskConfig` | `max_trade_risk_ratio`、`max_total_risk_ratio`、`max_position_ratio`、`max_sector_ratio`、`max_positions`、`cost_buffer_ratio`、`commission_per_order`、`max_round_trip_commission_ratio`、`min_trade_interval_sessions`、`emergency_threshold_min`、`emergency_threshold_max`、`emergency_threshold_default`、`price_deviation_min`、`price_deviation_max`、`price_deviation_default` |
| `execution` | `ExecutionConfig` | `unknown_lookup_delay`、`stop_immediate_retries`、`close_reconcile_delay` |
| `emergency` | `EmergencyConfig` | `daily_loss_breaker_ratio` |
| `health` | `HealthConfig` | `max_quote_delay`、`scheduler_heartbeat_timeout` |
| `marketdata` | `MarketDataConfig` | `reference_symbol`、`schedule`、`schedule_sessions` |
| `notification` | `NotificationConfig` | `confirmation_timeout`、`auto_execute_model_exits`、`confirm_emergency_exits` |
| `order_flow` | `OrderFlowConfig` | `retry_delay` |
| `patrol` | `PatrolConfig` | `interval` |
| `stockpool` | `StockPoolConfig` | `max_pool_size`、`max_watch`、`max_daily_changes`、`candidate_ttl_sessions`、`readd_cooldown_sessions`、`min_average_dollar_volume`、`allowed_exchanges`、`allowed_security_types` |
| `followups` | `FollowUpConfig` | `max_pending_per_stock`、`max_trigger_sessions`、`max_daily_requests` |
| `gateway` | `GatewayConfig` | `signal_horizons`、`max_pm_tasks`、`max_pm_rounds`、`max_critic_tasks`、`task_timeout`、`call_retries`、`subagent_call_timeout`、`call_timeout`、`breaker_failures`、`breaker_window`、`breaker_cooldown`、`fallback_blocked_decisions` |
| `scheduler` | `SchedulerConfig` | `min_cited_agents`、`max_concurrent_cases`、`max_resubmissions`、`max_challenge_rounds` |
| `heartbeat` | `HeartbeatConfig` | `min_review_sessions`、`max_review_sessions`、`default_review_sessions`、`max_postpone_loss`、`max_schedule_lag_sessions`、`max_deadline_sessions`、`max_daily_evaluations`、`min_viable_candidates`、`min_strong_candidates`、`expiring_sessions`、`exploration_cooldown_sessions` |
| `close_heartbeat` | `CloseHeartbeatConfig` | `interval`、`delay`、`refresh_gap` |
| `case_flow` | `CaseFlowConfig` | `recent_cases` |
| `queue_runner` | `QueueRunnerConfig` | `interval` |
| `snapshot` | `SnapshotConfig`；`app.py` 写的 `indexes` 改成默认值 | `indexes`、`news_sessions`、`filing_sessions` |
| `events` | `EventRulesConfig` | `price_threshold`、`candidate_price_threshold`、`holding_risk_ratio`、`volume_ratio`、`candidate_volume_ratio`、`volume_delay`、`news_sources`、`max_symbols`、`keywords`、`exclusions`、`filing_tiers`、`earnings_sessions`、`after_close_wait` |
| `event_detection` | `EventDetectionConfig` | `interval`、`min_gap`、`filing_interval`、`lookback` |
| `companydata` | `CompanyDataConfig` | `lookback_days`、`lookahead_days` |
| `screener` | `ScreenerConfig` | `top_n`、`min_market_cap`、`dollar_volume_sessions`、`relative_strength_sessions`、`benchmark`、`relative_strength_top`、`short_ma_sessions`、`long_ma_sessions`、`pullback_sessions`、`min_pullback`、`max_pullback`、`volume_recent_sessions`、`volume_base_sessions`、`min_volume_ratio`、`bar_delay` |

### 已经在 `config/models.toml`

模型、单价、厂商的接口、地址和 key，`gateway` 的 `general_budget`、`reserve_budget`、`exploration_budget`。

### 不是参数的固定值：留在代码里

| 固定值 | 在哪里 | 理由 |
| --- | --- | --- |
| API 路径、字段名、状态映射、`_HEADER`、`_US_PREFIX`、`_TIMES` | 各适配器 | 外部协议 |
| `_PAGE_SIZE`、`_SYMBOLS_PER_REQUEST`、`MAX_CONTENT`、`_VOLUME_STEP`、`_PLACES`、SEC 的 1 秒窗口 | Alpaca、moomoo、Discord、SEC 适配器 | 接口的上限和取值规则 |
| `_CENT`、`_SUB_DOLLAR_TICK` | `contracts/trade_plan.py`、替身 | 美股最小报价单位 |
| `_SETTLING`（开盘后 1 分钟） | `integrations/moomoo/quotes.py` | OpenD 快照刚开盘时 `last_price` 还是昨收，这是接口的行为；moomoo-quotes spec 的判断表写死它 |
| `max_retries=0`、`retries={"output": 0}` | `integrations/llm` | add-model-adapter 的 design D4：重试只由网关负责，用 `gateway.call_retries` 设定 |
| `_WAIT_SECONDS` | `workflows/case_workflow.py` | 等不到就接着等，改了行为不变 |
| `_STRONG_SCORE` 和评分 ≥ 0 | `modules/heartbeat/domain/close_check.py` | 评分档的含义，属于 signal-format spec 的协议 |
| `_MAJOR`、`_ORDINARY`、`_RANKS`、案件类型表、`OPENING_RULES`、`_INDUSTRIES_BY_SECTOR` | `modules` | 规则表和枚举的含义，改它们是改规则 |
| `_MILLION`、`_HUNDRED`、`_STRENGTH`、`_CHANGE` | `modules/gateway`、`modules/events`、Discord | 单位和取整 |
| 表名、列名、迁移、`PROGRESS`、`PLAN_TOPIC`、`DECISION_TOPIC`、`CONFIRMATION` | 各 `repo`、`workflows` | 已存数据和工作流的名字，改了读不到 |
| `APP_NAME`、`NOTE = "TAPro"`、`_BUTTON_PREFIX`、`_CLOSE_PLAN_NAMESPACE` | `bootstrap`、moomoo、Discord、紧急退出 | 系统的身份：改了认不出已有的工作流、提醒、按钮和方案 |
| `enable_patching`、`_DRIVER` | `bootstrap` | 恢复进行中的工作流和数据库驱动都只有一种正确值 |
| `ExecutionConfig.stagger_buys_and_stops` | `bootstrap/app.py` | Alpaca 不允许买单和止损卖单同时挂着，模拟盘只能打开，由券商决定，见 D5 |
| `FORMAT`、`QUIET`、日志级别 INFO | `bootstrap/logs.py` | bootstrap spec「日志」规定 |
| `NEW_YORK`、中文名称表和说明文字 | 各处 | 交易所的时区；显示用的文字 |
| `MODELS_FILE`、`SETTINGS_FILE`、`MODELS_TEMPLATE`、`MIGRATIONS` | `bootstrap` | 配置文件自己的位置，写进文件就读不到了 |
| `PYTHONUNBUFFERED=1`、`RestartSec`、`TimeoutStopSec`、`TimeoutStartSec`、`OnCalendar`、`KEEP`、compose 的端口和健康检查、OpenD 的端口 | `deploy/` | 主程序以外的进程读，主程序读不到；deployment spec「部署的配置项」已经规定每项只在一处 |

`src/` 里读环境变量的地方全部去掉。第三方库自己读环境变量不算。

## Decisions

### D1 一个 `config/settings.toml`，每个配置类一节

文件和 `config/models.toml` 放在一起，同样不进 git、启动时生成、0600、改完重启生效。

- 密钥单独一个文件：两个文件都在 `config/`、都是 0600，分开没有好处，部署要多记一个文件
- 写进 `models.toml`：模型文件有自己的结构和模板，参数多了以后换模型时不好找

### D2 默认值留在代码里，文件只放改动的项

生成的文件把默认值写成注释，去掉注释才生效。这样代码改默认值时，没动过的项跟着变，文件不会成为第二份默认值。

- 把全部值写进文件：每次改默认值都要改每个部署的文件，文件里的值谁也说不清是不是有意改的

### D3 `Settings` 类同时是校验的模式和生成的清单

`Settings` 是一个 Pydantic 类，每个字段是一节，类型是那份配置类。校验、未知项报错都用 Pydantic；生成文件时遍历它的字段，不另写清单。

```python
class Settings(BaseModel):
    database: DatabaseConfig
    alpaca: AlpacaConfig
    ...
    risk: RiskConfig = RiskConfig()
```

`settings_text.py` 从 `Settings` 生成文本，说明取自配置类的 docstring 和字段的注释（`inspect.getsource`），写成 `##` 开头，和写成 `#` 的默认值分开。

- 手写模板：第二份清单，加配置项时会漏
- 把注释改成 `Field(description=...)`：要改 33 个文件的每个字段，运行时用不到
- 生成时写字段的类型和范围：范围在代码和 spec 里，文件只要能看懂、能改

### D4 值的写法

- 时长写秒数：`timeout = 10`，生成的行尾带 `# 秒`。Pydantic 按秒读
- 比例和金额：`tomllib.loads(text, parse_float=Decimal)`，和模型文件一样精确
- 日期写 TOML 的日期，枚举写值，`frozenset` 排序后写成数组，模型写成行内表
- 模型的数组一行一个，比如 `events.keywords`、`marketdata.schedule`

字符串用 `json.dumps(ensure_ascii=False)` 写：JSON 字符串是合法的 TOML 字符串。

### D5 别处设定的项不能写在这里

`ELSEWHERE` 列出 spec「文件的内容」的那张表：`gateway` 的三个额度、`alpaca_bars` 和 `alpaca_news` 的 key、`execution.stagger_buys_and_stops`。生成时跳过它们，读的时候写了就报错。

- 三个额度：`load_models(path, gateway)` 收配置文件读到的网关配置，用 `GatewayConfig.model_validate` 合上文件里的额度，额度的校验照常
- Alpaca 的 key：读文件时把 `alpaca` 的两项抄进另外两节，三份配置类不用改
- 错开：`paper_execution` 用 `model_copy` 打开它，模拟盘只有这一种正确值

把三份 Alpaca 配置合并成一个类能少一处特例，但要改两个适配器、它们的 spec 和测试，和这个变更的目的无关。

### D6 出错的处理（问题 1：生成的文件没有密钥）

第一次启动时生成文件，九个必填项是空的，一次列出全部缺少的项，启动失败。不能用替身代替：数据库和券商没有替身。

- 先查缺少：`Settings` 的字段里没有默认值的项，没写或为空都算缺少，全部列出。空字符串是生成时写的占位
- 再交给 Pydantic：一次报出全部写错的项，用 `errors(include_input=False, include_url=False, include_context=False)` 拼成 `项：原因`，`from None` 抛 `SettingsError`
- 必填项写成字符串也能读：`guild_id = "3003"` 由 Pydantic 转成整数，所以「都写在引号里」是一条够用的说明
- 数据库地址的校验抛不带输入的错误：`make_url` 的异常里有地址

这样 `load_settings`「错误里只有变量名」的规则保留下来，比 `hide_input_in_errors` 更严：连 Pydantic 的 `input` 字段都不取。

### D7 启动的第 1 步读两个文件（问题 2）

第 1 步从「读环境变量」改成「读配置文件」：先生成还不存在的两个文件，再读配置文件和模型文件。模型文件从第 4 步提前到第 1 步。

- 两个文件都写错时，第一次启动就都生成好，不用跑两次
- 配置错误不碰数据库和券商，也不留下启动失败的程序日志
- `load_models` 要用读到的网关配置，放在一起顺序最简单
- `LlmClient` 留在第 4 步：它开线程，要登记关闭

`run(stop, externals, config)` 和 `start(externals, config, stack)` 去掉 `environ`。`BootstrapConfig` 只剩 `models_file`、`settings_file` 两个路径，全程序测试指向自己的文件；交易日历的范围挪到 `calendar` 一节。

- 模型文件留在第 4 步：两个文件的错误分两步报，第一次部署要启动两次才看全

### D8 和默认值不同的项写一条 INFO

读完配置文件以后写一条，比较 `Settings` 的值和字段的默认值。必填项没有默认值，不参与比较，所以这条日志不会有密钥。值用和文件相同的写法。

### D9 `postgres.env` 和 `opend.env` 留在 `/etc/tradingagentspro/`（问题 3）

它们不是主程序读的：`POSTGRES_PASSWORD` 由 docker compose 的 `env_file` 读，`MOOMOO_LOGIN_ACCOUNT` 由 systemd 的 `EnvironmentFile` 读，都在主程序启动以前。

- 收进 `config/settings.toml`：Docker 和 systemd 读不了 TOML，要再写一步转换，开机顺序也要等它
- 把这两个文件挪到 `config/`：格式还是 env，读的人不变，没有统一什么；`config/` 又是第一次启动才有的，状态库在主程序以前启动
- 数据库密码因此写在两处（`postgres.env` 和 `database.url`），和现在一样。这个密码只在第一次初始化数据卷时用

### D10 主机手工迁移（问题 4）

`/etc/tradingagentspro/tradingagentspro.env` 只有 root 能读，单元文件也要 root 装，所以迁移由人做，步骤写进两份部署说明的「迁移到配置文件」，见 Migration Plan。旧文件在确认启动完成以后再删，回退时还用得上。

### D11 归档顺序（问题 5）

`add-model-adapter` 先归档，这个变更后归档。`bootstrap` 的「日志」「模拟盘的装配」和 `deployment` 的「密钥」两边都改，这里的 MODIFIED 是在它的文字上改的，后归档才不会丢掉它的改动。`model-config` 两边改的是不同的 requirement，互不影响。

OpenSpec 的 MODIFIED 不允许去掉场景，所以三个场景只是改名的，直接在主 spec 里改了名字：discord-notification 的「4 个必填项齐全」「缺少必填项」、program-log 的「读配置文件失败时不写」。bootstrap spec 的 Purpose 也直接改了。

### D12 地址和写死的值

- `SecConfig` 加 `base_url`、`data_url`，`CboeConfig` 加 `url`，和 Alpaca、Finnhub 的 `base_url` 一样，默认值用原来的常量
- `SnapshotConfig.indexes` 的默认值改成 SPY、QQQ、VIXY：线上用的就是这三个，默认值要等于线上的值，生成的文件才不改变行为
- `DiscordConfig.from_env` 和它的变量名常量去掉。冒烟测试继续读 `dev.env`，自己构造配置

### 省略的情况

- 已经生成的文件不跟着代码更新：注释里的默认值可能过时，新加的参数不出现在文件里，用代码的默认值
- 以后加了必填项：启动失败，列出缺的项，手工写上
- 节写成不是表（`alpaca = 1`）：启动失败，错误信息不好看
- 改了配置时进行中的工作流：和改代码一样，不专门处理
- 工作目录不对时在别处生成文件：主程序只由 systemd 和仓库根目录启动
- 文件的权限不对：只在生成时设，不检查已有的文件
- 配置项之间跨配置类的关系：只有各配置类自己的校验

## Risks / Trade-offs

- [服务用户能读到全部密钥，能以服务用户身份运行命令的管理员（包括经 SSH 的 Claude）也能] → 主程序本来就要拿到这些值，用户已经接受，和 add-model-adapter 的 D10 相同
- [`config/` 不在备份里，丢了要重写全部密钥] → 部署说明写明；数据库密码和 key 都能在各家后台重新生成，数据库密码在 `postgres.env` 里还有一份
- [部署新代码以前没有迁移，主程序启动失败、30 秒重启一次] → 部署说明把迁移放在更新以前；失败发生在第 1 步，不碰数据库和券商
- [改错一个参数造成启动失败] → 错误日志列出项名；线上不改就和现在一样
- [`inspect.getsource` 依赖源码] → 主程序从仓库克隆运行，源码都在；打包的 wheel 里也有 `.py`
- [配置文件比 root 才能读的环境变量文件宽松] → 文件 0600，只有服务用户和 root 能读

## Migration Plan

这些步骤写进部署说明，由人在部署机器上做，线上的数值不变：

1. 备份状态库
2. 停止主程序
3. 以服务用户拉代码、同步依赖，在 `~/app` 运行一次 `tradingagentspro run`：生成 `config/settings.toml`，列出九个必填项后退出，什么都没有连接
4. 把 `/etc/tradingagentspro/tradingagentspro.env` 的九个值填进 `config/settings.toml`
5. 安装新的 `tradingagentspro.service`，`daemon-reload`，启动主程序，看到「启动完成」
6. 确认没问题以后删掉 `/etc/tradingagentspro/tradingagentspro.env`

回退：第 6 步以前，回到上一个提交，装回旧的单元。留下的 `config/` 没有影响。
