# bootstrap Specification

## Purpose
启动与装配用一条命令启动交易侧程序：从环境变量读配置、配置日志，按固定的顺序连接数据库、券商、行情和 Discord，把已经实现的模块装配起来，做完启动对账和第一次健康判定；收到停止信号时按相反的顺序关闭，并用退出码告诉进程管理器结果。

## Requirements

### Requirement: 命令行

程序 SHALL 提供命令 `tradingagentspro`：

| 用法 | 行为 |
| --- | --- |
| `tradingagentspro run` | 启动交易侧程序（模拟盘），一直运行到收到停止信号，见「启动顺序」「停止」 |
| `tradingagentspro --version` | 打印 `tradingagentspro <版本号>`，退出码 0，不读环境变量、不连接任何东西 |
| 没有子命令，或者子命令、选项不认识 | 打印用法，退出码 2，不连接任何东西 |

退出码 SHALL 按下表：

| 退出码 | 什么时候 |
| --- | --- |
| 0 | 收到停止信号以后正常停止 |
| 1 | 启动失败，见「启动失败」 |
| 2 | 命令行用法不对 |

本 spec 的场景除非另有说明，都使用下面的基准数据：

- 启动时刻是 2026-10-14T14:00:00Z（周三，美东 10:00，常规交易时段开盘后 30 分钟）
- 环境变量全部给出，格式正确
- 状态库是一个空的数据库
- Alpaca 模拟盘：没有持仓，没有挂单，账户设置的请求正常受理
- OpenD：参考股票 SPY 的行情 10 秒前更新
- Discord：登录、注册 `/plan`、核对频道都正常
- "正常启动"指在基准数据下运行 `tradingagentspro run`

#### Scenario: 打印版本号

- **WHEN** 运行 `tradingagentspro --version`
- **THEN** 输出 `tradingagentspro <版本号>`，退出码 0
- **AND** 没有读环境变量，没有连接数据库、Alpaca、OpenD、Discord

#### Scenario: 用法不对

- **WHEN** 运行 `tradingagentspro`（没有子命令），或者 `tradingagentspro start`
- **THEN** 打印用法，退出码 2，没有连接任何东西

#### Scenario: 正常停止的退出码

- **GIVEN** 正常启动
- **WHEN** 收到 SIGTERM
- **THEN** 退出码 0

#### Scenario: 启动失败的退出码

- **GIVEN** 没有 `APCA_API_SECRET_KEY`
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 退出码 1

### Requirement: 环境变量

程序 SHALL 在启动的第一步一次读完下表的环境变量，本系统的代码之后不再读环境变量：

| 变量 | 含义 | 格式 |
| --- | --- | --- |
| `TRADINGAGENTSPRO_DATABASE_URL` | 状态库的数据库地址，含用户名和密码 | `postgresql://用户:密码@主机:端口/数据库`；驱动一律用 psycopg，写不写 `+psycopg` 都可以 |
| `APCA_API_KEY_ID` | Alpaca 模拟盘的 key ID | 文本 |
| `APCA_API_SECRET_KEY` | Alpaca 模拟盘的 secret | 文本 |
| `DISCORD_BOT_TOKEN` | Discord 机器人的 token | 文本 |
| `DISCORD_GUILD_ID` | 服务器 ID | 正整数 |
| `DISCORD_CHANNEL_ID` | 告警和确认的频道 ID | 正整数 |
| `DISCORD_USER_ID` | 唯一可以操作的用户 ID | 正整数 |

- 没有设置或为空都算缺少。有缺少的变量时 SHALL 一次列出全部缺少的变量名，启动失败
- 格式不对时 SHALL 写明是哪个变量，启动失败
- 错误信息和日志里 MUST NOT 出现这些变量的值。数据库地址只能以密码换成 `***` 的形式出现在日志里
- 读环境变量失败时，SHALL NOT 连接数据库、Alpaca、OpenD、Discord

#### Scenario: 全部给出

- **WHEN** 正常启动
- **THEN** 启动完成，七个变量都用上了：用数据库地址连接状态库，用两个 Alpaca 变量调用 Alpaca，用四个 Discord 变量连接 Discord

#### Scenario: 一次列出全部缺少的变量

- **GIVEN** 没有 `APCA_API_SECRET_KEY`，`DISCORD_USER_ID` 是空字符串
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志在一条里列出 `APCA_API_SECRET_KEY` 和 `DISCORD_USER_ID`
- **AND** 没有连接数据库、Alpaca、OpenD、Discord

#### Scenario: Discord 的 ID 不是正整数

- **GIVEN** `DISCORD_CHANNEL_ID` 是 `general`
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明 `DISCORD_CHANNEL_ID` 必须是正整数，没有它的值

#### Scenario: 数据库地址写错

- **GIVEN** `TRADINGAGENTSPRO_DATABASE_URL` 是 `pw-9f3k@db`（不是数据库地址）
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明 `TRADINGAGENTSPRO_DATABASE_URL` 不是数据库地址
- **AND** 日志里没有 `pw-9f3k`

#### Scenario: 数据库地址不写驱动

- **GIVEN** 数据库地址写成 `postgresql://用户:密码@主机:端口/数据库`
- **WHEN** 正常启动
- **THEN** 用 psycopg 连上数据库，启动完成

### Requirement: 日志

程序 SHALL 把日志写到 stderr，每条一行开头依次是：UTC 时间（ISO 8601，到毫秒，以 `Z` 结尾）、级别、线程名、logger 名，然后是内容。异常带上异常栈。

- 级别 SHALL 是 INFO：DEBUG 不输出
- httpx 和 httpcore SHALL 只输出 WARNING 及以上：它们每个请求写一条 INFO，Alpaca 的每次查询都会写
- DBOS 的日志 SHALL 走同一个输出、同一种格式，每条只出现一次
- 日志 MUST NOT 含有环境变量的值，见「环境变量」
- 下面这些 SHALL 各写一条日志：程序启动（版本号和模式）、启动顺序里每一步的结果、启动完成、收到停止信号、每一项关闭、已停止。正常的结果用 INFO，有不一致和降级用 WARNING，失败用 ERROR

#### Scenario: 时间用 UTC

- **GIVEN** 进程的本机时区是 UTC+14
- **WHEN** 在 2026-10-14T14:00:00.123Z 写一条 INFO 日志
- **THEN** 这一行以 `2026-10-14T14:00:00.123Z INFO` 开头，后面是线程名、logger 名和内容

#### Scenario: httpx 的请求日志不输出

- **WHEN** httpx 写一条 INFO 的请求日志，然后写一条 WARNING
- **THEN** 只有 WARNING 那条出现在 stderr

#### Scenario: DEBUG 不输出

- **WHEN** 程序自己的 logger 写一条 DEBUG 和一条 INFO
- **THEN** 只有 INFO 那条出现在 stderr

#### Scenario: DBOS 的日志

- **WHEN** 正常启动
- **THEN** DBOS 启动时的 INFO 日志出现在 stderr，格式和程序自己的日志相同，每条只出现一次

#### Scenario: 日志里没有密钥

- **GIVEN** `APCA_API_SECRET_KEY`、`DISCORD_BOT_TOKEN` 和数据库密码各是一个不会在别处出现的值
- **WHEN** 正常启动再停止；另外在 Alpaca 拒绝账户设置（401）时启动；另外在数据库连不上时启动
- **THEN** 三次运行的 stderr 里都没有这三个值
- **AND** 日志里的数据库地址，密码是 `***`

### Requirement: 启动顺序

`tradingagentspro run` SHALL 按下表的顺序启动，前一步完成才做下一步，全部完成后写「启动完成」，然后一直运行到收到停止信号：

| 步骤 | 做什么 | 失败时 |
| --- | --- | --- |
| 1 读环境变量 | 见「环境变量」 | 启动失败 |
| 2 数据库 | 连接状态库，升级到最新的迁移版本（state-store spec「表的归属和迁移」） | 启动失败 |
| 3 交易日历 | 按「交易日历的加载范围」加载纽交所日历 | 启动失败 |
| 4 装配 | 按「模拟盘的装配」建出全部部件，开始连接 OpenD（不等连上） | 启动失败 |
| 5 Alpaca 账户设置 | 不做空、最大保证金倍数 1（alpaca-broker spec「账户设置」） | 启动失败 |
| 6 启动对账 | 见「启动对账」 | 读取失败时启动失败；有不一致时照常继续 |
| 7 第一次健康判定 | 见「第一次健康判定」 | 不会失败；OpenD 连不上、处于降级模式都照常继续 |
| 8 Discord | 登录、注册 `/plan`、连接、核对频道（discord-notification spec、manual-plans spec） | 启动失败 |
| 9 DBOS | 见「DBOS」 | 启动失败 |
| 10 盘中巡检 | 开始盘中巡检（session-patrol spec「巡检的开始、间隔和停止」）；第一轮在巡检自己的线程里进行，启动不等它做完 | 启动失败 |

- 对账和健康判定 SHALL 在 Discord 和 DBOS 之前完成：`/plan`、按钮和 DBOS 恢复的工作流是新订单仅有的入口
- Alpaca 账户设置 SHALL 在对账之前：对账可能补挂止损单，补挂之前账户要已经是不做空的
- Discord SHALL 在 DBOS 之前：DBOS 恢复的工作流可能马上要请求确认
- 盘中巡检 SHALL 最后开始：它要调用券商、风控和 Discord，这些都要已经就绪，启动对账也要已经做完；停止时它最先停（见「停止」）

#### Scenario: 正常启动的顺序

- **WHEN** 正常启动
- **THEN** 日志依次写出：程序启动、数据库已升级、交易日历的范围、Alpaca 账户设置完成、启动对账没有不一致、健康正常、Discord 已连接、DBOS 已启动、盘中巡检已开始（写明间隔）、启动完成
- **AND** Alpaca 先收到账户设置，再收到读取挂单和持仓的请求

#### Scenario: 空数据库启动不改动券商

- **GIVEN** Alpaca 模拟盘有一笔不认得的挂单和一只不认得的持仓
- **WHEN** 正常启动
- **THEN** Alpaca 收到的写请求只有账户设置一个，其余都是读取
- **AND** 启动完成

#### Scenario: 启动以后巡检跟进停机前提交的订单

- **GIVEN** 状态库里有一笔已提交的买单 P1（买入 BBB 100 股，止损价 45），风控为它预留了 100 股；停机期间 Alpaca 那边 P1 全部成交，持仓 BBB 100 股
- **WHEN** 正常启动
- **THEN** 启动对账跟进 P1，P1 全部成交，挂上 BBB 的止损单
- **AND** 巡检的第一轮把 P1 的累计成交报给风控，P1 没有有效预留

### Requirement: 启动失败

「启动顺序」里写着启动失败的步骤失败时，程序 SHALL：

1. 写一条 ERROR 日志：哪一步失败，原因（异常的类型和信息），带异常栈
2. 不做后面的步骤
3. 按打开的相反顺序关闭已经打开的部分，规则同「停止」
4. 以退出码 1 退出，由进程管理器决定是否重启

| 失败 | 步骤 | 例子 |
| --- | --- | --- |
| 环境变量缺少或格式不对 | 1 | 见「环境变量」 |
| 数据库连不上 | 2 | 地址或密码不对，PostgreSQL 没有运行 |
| 数据库升级失败 | 2 | 迁移的建表语句报错；升级在一个事务里，失败时数据库不变 |
| Alpaca 认证失败 | 5 | key 不对，Alpaca 回答 401 或 403 |
| Alpaca 不可用 | 5、6 | 超时、连不上、429、5xx |
| Discord 启动失败 | 8 | token 无效、注册 `/plan` 失败、频道不对、超过连接超时（默认 30 秒） |
| DBOS 启动失败 | 9 | 建 `dbos` schema 失败 |

OpenD 没有运行或没有登录 SHALL NOT 算启动失败，见「第一次健康判定」。

#### Scenario: 数据库连不上

- **GIVEN** 数据库地址指向一个没有 PostgreSQL 的端口
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明数据库这一步和连接的错误
- **AND** 没有连接 Alpaca、OpenD、Discord

#### Scenario: 数据库升级失败

- **GIVEN** 数据库里已经有一张 `risk_reservations` 表，但没有迁移版本的记录
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明数据库这一步和建表的错误
- **AND** 数据库里没有迁移版本的记录，没有新建别的表
- **AND** 没有连接 Alpaca、OpenD、Discord

#### Scenario: Alpaca 认证失败

- **GIVEN** Alpaca 对账户设置的请求回答 401
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明 Alpaca 账户设置这一步和 401
- **AND** Alpaca 没有收到读取挂单和持仓的请求，Discord 没有连接，DBOS 没有启动
- **AND** OpenD 行情连接和数据库连接已关闭，日志依次写出这两项关闭

#### Scenario: 账户设置时 Alpaca 超时

- **GIVEN** 账户设置的请求超时
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明 Alpaca 账户设置这一步和券商不可用
- **AND** 没有对账，Discord 没有连接

#### Scenario: 对账时 Alpaca 不可用

- **GIVEN** 启动对账读取全部挂单时连不上
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明启动对账这一步和券商不可用
- **AND** Discord 没有连接，DBOS 没有启动

#### Scenario: Discord 启动失败

- **GIVEN** Discord 登录失败（token 无效）
- **WHEN** 运行 `tradingagentspro run`
- **THEN** 启动失败，错误日志写明 Discord 这一步和登录失败
- **AND** DBOS 没有启动，数据库里没有 `dbos` schema
- **AND** OpenD 行情连接和数据库连接已关闭

### Requirement: 数据库

程序 SHALL 用 `TRADINGAGENTSPRO_DATABASE_URL` 连接状态库（会话时区 UTC，见 state-store spec），在调用任何外部系统之前升级到最新的迁移版本。DBOS SHALL 用同一个数据库，它的表放在 `dbos` schema 里，和本系统在 `public` 里的表分开。

#### Scenario: 空数据库

- **WHEN** 正常启动
- **THEN** 数据库里有 state-store spec 和 trade-records spec 规定的全部表，迁移版本是最新的
- **AND** 有 `dbos` schema，里面是 DBOS 的表

#### Scenario: 再次启动

- **GIVEN** 正常启动再停止过一次，状态库里有一笔订单记录
- **WHEN** 再正常启动一次
- **THEN** 启动完成，表和这笔订单记录都不变

### Requirement: 交易日历的加载范围

程序 SHALL 在启动时加载一次纽交所日历，全部模块和适配器共用这一份。加载范围可以配置：

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 起点 | 2020-01-01 | 覆盖系统上线前后用到的历史（trading-calendar spec） |
| 终点 | 启动时刻往后 730 天 | 远超最长的前瞻（60 个交易日）；每次重启重新加载 |

#### Scenario: 默认的加载范围

- **WHEN** 在 2026-10-14T14:00:00Z 正常启动
- **THEN** 日历的第一个交易日是 2020-01-02，最后一个交易日是 2028-10-13
- **AND** 日志写出这个范围

#### Scenario: 改了加载范围

- **GIVEN** 配置的起点是 2026-01-01，往后 30 天
- **WHEN** 在 2026-10-14T14:00:00Z 正常启动
- **THEN** 日历的第一个交易日是 2026-01-02，最后一个交易日是 2026-11-13

### Requirement: 模拟盘的装配

`tradingagentspro run` SHALL 按模拟盘装配，各部件如下。全部模块共用同一个系统时钟和同一份交易日历；阈值都取各自配置的默认值，不读配置文件。

| 部件 | 装配 |
| --- | --- |
| 券商 | Alpaca 模拟盘：普通订单和止损单两个适配器共用一个客户端，key 来自环境变量；估算费用用的每笔佣金 SHALL 等于风控配置的每笔佣金（默认 2） |
| 执行模块 | 订单、止损保护、交易记录存在状态库；执行配置的「买单和止损单错开」SHALL 打开（Alpaca 不允许同一只股票的买单和止损卖单同时挂着），其余用默认值 |
| 风控 | 预留存在状态库；风控配置用默认值 |
| 紧急退出、健康监控 | 各自配置的默认值 |
| 行情 | 行情源是 OpenD（默认 127.0.0.1:11111）；行情配置用默认值（参考股票 SPY） |
| 通知 | 通知通道是 Discord 机器人，配置来自环境变量；通知配置用默认值（确认超时 30 分钟） |
| `/plan` | 方案 ID 用随机 UUID；提交接口和确认按钮的回调是下单流程的（order-flow spec「提交方案」「按钮的结果只送给在等确认的方案」） |
| 锁 | 全程序一把进程内的锁（session-patrol spec「执行模块和风控的调用串行」）；巡检和下单流程用的是同一把 |
| 盘中巡检 | 调用上面装配好的执行模块、风控和通知，用这把锁；巡检配置用默认值（间隔 15 秒） |
| 下单流程 | 调用上面装配好的执行模块、风控、紧急退出、健康监控、行情和通知，用这把锁；下单流程配置用默认值（重试间隔 30 秒） |

#### Scenario: 打开买单和止损单错开

- **WHEN** 按模拟盘装配券商
- **THEN** 执行模块的「买单和止损单错开」是打开的

#### Scenario: 估算费用用风控的每笔佣金

- **GIVEN** 风控配置的每笔佣金是默认值 2；Alpaca 那边有一笔全部成交的买单和一张全部成交的止损单
- **WHEN** 按模拟盘装配的两个券商适配器查询这两笔
- **THEN** 两笔回报的费用都是 2

#### Scenario: 巡检和以后的流程共用一把锁

- **WHEN** 正常启动
- **THEN** 巡检的间隔是 15 秒
- **AND** 启动给以后的流程用的锁，就是巡检用的那一把：另一个线程拿着它时，巡检不调用执行模块和风控

#### Scenario: 录入并确认一个买入方案

- **GIVEN** 正常启动；Alpaca 模拟盘账户的权益和现金都是 10,000，前一交易日收盘时的权益 10,000；OpenD 有 BBB 的行情，最新价 50
- **WHEN** 本人运行 `/plan buy symbol:BBB sector:医疗 quantity:20 limit:50 stop:45`
- **THEN** 回复风控通过：买入 BBB 20 股；频道里出现这个方案的确认消息
- **WHEN** 本人按确认消息的「确认」
- **THEN** 消息显示已确认
- **AND** Alpaca 收到一笔买单：客户端订单 ID 是这个方案的 ID，BBB 20 股，限价 50

### Requirement: 启动对账

Alpaca 账户设置之后、Discord 和 DBOS 之前，程序 SHALL 让执行模块和券商对账一次（reconciliation spec「对账」），按结果处理：

| 结果 | 处理 |
| --- | --- |
| 没有不一致 | 写 INFO，继续启动 |
| 有不一致 | 写 WARNING，列出三类不一致（股票、客户端订单 ID、股数）；继续启动。执行模块按 reconciliation spec「有不一致时暂停开仓」暂停开仓。本变更不发告警 |
| 读取券商失败 | 启动失败，见「启动失败」 |

对账的结果 SHALL 保留下来，给以后的告警使用。

#### Scenario: 没有不一致

- **WHEN** 正常启动
- **THEN** 日志写出启动对账没有不一致，启动完成

#### Scenario: 来历不明的持仓

- **GIVEN** Alpaca 模拟盘持有 CCC 100 股，状态库里没有 CCC 的订单
- **WHEN** 正常启动
- **THEN** 启动完成
- **AND** 有一条 WARNING：启动对账有不一致，来历不明的持仓 CCC 100 股
- **AND** 保留的启动对账结果里有这项不一致

#### Scenario: 不认得的挂单

- **GIVEN** Alpaca 模拟盘挂着一笔买入 DDD 的订单，客户端订单 ID 是 X1，状态库里没有它
- **WHEN** 正常启动
- **THEN** 启动完成，有一条 WARNING 写明不认得的挂单 DDD、X1
- **AND** 这笔挂单没有被撤销

### Requirement: 第一次健康判定

启动对账之后、Discord 和 DBOS 之前，程序 SHALL 让健康监控判定一次（degraded-mode spec），健康信号如下：

| 信号 | 取值 | 理由 |
| --- | --- | --- |
| PM、Critic 还有可用的模型 | 有 | 还没有 Agent 网关 |
| 持仓保留预算用完 | 没有 | 还没有 Agent 网关 |
| 调度器最近一次心跳 | 判定的时刻 | 还没有调度器 |
| 券商连接 | 正常 | 启动对账刚读过券商；读取失败时已经启动失败 |
| 行情最近一次更新的时刻 | 行情模块给出的参考股票的更新时刻；取不到（OpenD 没开、没登录、出错）时为没有，并写一条 WARNING 写明原因 | 和以后的定时判定取法相同（market-data spec「行情最近一次更新的时刻」） |

| 判定结果 | 处理 |
| --- | --- |
| 正常 | 写 INFO，继续启动 |
| 降级 | 写 WARNING，列出异常的检查；继续启动。风控按 degraded-mode spec 暂停开仓。本变更不发告警 |

#### Scenario: 盘中行情正常

- **WHEN** 正常启动
- **THEN** 健康是正常，启动完成

#### Scenario: 盘中 OpenD 没开

- **GIVEN** 取行情时 OpenD 报错
- **WHEN** 正常启动
- **THEN** 有一条 WARNING 写明取不到行情和 OpenD 的错误
- **AND** 进入降级模式，异常的检查是数据延迟，有一条 WARNING 写明
- **AND** 启动完成

#### Scenario: 盘外 OpenD 没开

- **GIVEN** 启动时刻是 2026-10-14T12:00:00Z（美东 8:00，开盘前）；取行情时 OpenD 报错
- **WHEN** 正常启动
- **THEN** 有一条 WARNING 写明取不到行情
- **AND** 健康是正常（盘外不检查数据延迟），启动完成

#### Scenario: 开盘后行情一直没有更新

- **GIVEN** 参考股票的行情停在前一个交易日收盘
- **WHEN** 正常启动
- **THEN** 进入降级模式，异常的检查是数据延迟，启动完成

### Requirement: DBOS

Discord 启动之后，程序 SHALL 用状态库所在的数据库启动 DBOS，应用名 `tradingagentspro`；DBOS 的表放在 `dbos` schema。

- 启动 DBOS 之前 SHALL 把下单流程用到的部件（见「模拟盘的装配」）交给下单流程，启动时由 DBOS 恢复上次没做完的下单流程（order-flow spec「持久化和恢复」）
- 进行中的下单流程 SHALL NOT 因为程序版本变了就不再恢复：程序更新以后重启，照样恢复
- 停止时 SHALL 在盘中巡检停下以后关闭 DBOS（见「停止」），不等还在运行的工作流结束（它们由下次启动的 DBOS 恢复）

#### Scenario: 启动和停止 DBOS

- **WHEN** 正常启动再停止
- **THEN** 日志依次写出 DBOS 已启动、启动完成、收到停止信号、已关闭盘中巡检、已关闭 DBOS
- **AND** 数据库里有 `dbos` schema

#### Scenario: 启动时恢复等开盘的方案

- **GIVEN** 上次运行时，一个买入 BBB 的方案在盘后确认，下单流程在等下一个开盘；然后程序停止
- **WHEN** 在下一个交易日盘中正常启动
- **THEN** 启动完成以后 Alpaca 收到这个方案的买单，此前没有再请求确认

#### Scenario: 程序更新以后恢复

- **GIVEN** 上次运行时有一个在等开盘的下单流程；之后程序更新到新版本，下单流程的步骤没有变
- **WHEN** 在下一个交易日盘中正常启动
- **THEN** 这个下单流程照样恢复，Alpaca 收到这个方案的买单

### Requirement: 停止

程序启动完成以后收到 SIGTERM 或 SIGINT，SHALL：

1. 写一条 INFO：收到停止信号，开始停止
2. 按打开的相反顺序关闭：盘中巡检 → DBOS → Discord → OpenD 行情连接 → 数据库连接。每一项关闭写一条 INFO。盘中巡检按 session-patrol spec「巡检的开始、间隔和停止」停下：正在做的那一项做完才算关闭
3. 某一项关闭出错时写 ERROR（带异常栈），接着关闭下一项
4. 全部关完写「已停止」，以退出码 0 立即结束进程：SHALL NOT 等还在等确认、等开盘、等重试的下单流程（它们由下次启动的 DBOS 恢复，order-flow spec「持久化和恢复」）

启动过程中收到停止信号时，程序 SHALL 做完当前的启动（每一步都有超时），启动完成以后不再等待、直接停止；启动失败时按「启动失败」处理。停止过程中再收到停止信号，不另外处理。

#### Scenario: SIGTERM

- **GIVEN** 正常启动
- **WHEN** 收到 SIGTERM
- **THEN** 日志依次写出：收到停止信号、已关闭盘中巡检、已关闭 DBOS、已关闭 Discord、已关闭 OpenD 行情连接、已关闭数据库连接、已停止
- **AND** 退出码 0

#### Scenario: SIGINT

- **GIVEN** 正常启动，在终端里运行
- **WHEN** 按 Ctrl-C
- **THEN** 和收到 SIGTERM 一样停止，退出码 0，没有 KeyboardInterrupt 的异常栈

#### Scenario: 某一项关闭出错

- **GIVEN** 正常启动；关闭 OpenD 行情连接时报错
- **WHEN** 收到 SIGTERM
- **THEN** 有一条 ERROR 写明关闭 OpenD 行情连接出错
- **AND** 数据库连接照样关闭，日志写出已停止，退出码 0

#### Scenario: 启动过程中收到停止信号

- **GIVEN** 正在连接 Discord 时收到 SIGTERM
- **WHEN** 启动的其余步骤完成
- **THEN** 写出启动完成以后立即停止，按顺序关闭，退出码 0

#### Scenario: 停止时巡检正在跟进订单

- **GIVEN** 正常启动；巡检正在跟进一笔订单，Alpaca 对这次查询还没有响应
- **WHEN** 收到 SIGTERM
- **THEN** Alpaca 的响应回来、这笔订单保存以后才写出已关闭盘中巡检
- **AND** 之后才关闭 DBOS、Discord、OpenD 行情连接和数据库连接，退出码 0

#### Scenario: 停止时有方案在等确认

- **GIVEN** 正常启动；一个方案的确认消息已经发出，还有 25 分钟才到截止时刻
- **WHEN** 收到 SIGTERM
- **THEN** 按顺序关闭，写出已停止，退出码 0，没有等到截止时刻
- **AND** 写出已停止以后进程立即结束；这个方案的下单流程由下次启动的 DBOS 恢复（order-flow spec「持久化和恢复」）
