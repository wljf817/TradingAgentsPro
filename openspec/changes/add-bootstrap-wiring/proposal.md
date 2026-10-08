# Proposal

## Why

M2 已经有了状态库、交易记录、Alpaca 模拟盘适配器、moomoo 行情、Discord 告警和 `/plan`，但它们只在各自的测试里构造，`tradingagentspro` 命令还只会打印版本号，没有一个能运行的程序。后面的「下单流程」「紧急退出流程」「收盘对账」「健康监控接线」都要在一个已经装配好、能启动和停止的进程里加流程；「部署」要知道程序怎么启动、读哪些环境变量、怎样退出。M1 和 M2 各变更留给「启动与装配」的事项（启动时先升级数据库、先做 Alpaca 账户设置再对账、先判定一次健康再接受新订单、Alpaca 上打开买单和止损单错开、配置 Python 日志等）也都在等这一步。

本变更的目的只有一个：**用一条命令 `tradingagentspro run` 启动交易侧程序：读环境变量、配置日志，按固定的顺序把已经实现的部分装配起来、做完启动时的对账和健康判定，收到 SIGTERM 或 SIGINT 时按相反的顺序关闭。** 装配好以后还不会下单，流程由后面的变更加上。

引用的 Proposal（完整版）章节：

- 「技术栈与部署」：
  - DBOS 一行：工作流、队列和人工确认的等待都用 DBOS
  - 状态库一行：PostgreSQL 同时作为 DBOS 的状态存储
  - 券商一行：实盘和模拟盘实现同一套券商接口，只在启动时切换；模拟盘上把买单和止损单错开
  - 部署一行：一台常开机器，Docker 运行 PostgreSQL，另外运行 OpenD 和主程序
  - 可靠性要求：API key 和 Discord token 存环境变量；进程崩溃后由 DBOS 从断点恢复，执行模块恢复后先与券商对账
- 「故障处理」：系统健康由健康监控模块判定；降级模式下暂停开仓；恢复后先对账
- 「记录、复盘与经验 · 程序日志」：系统的启动、停止要记下来。程序日志 M4 才有，M2 写进 Python 日志（已决定）

## What Changes

- 命令行：`tradingagentspro run` 启动程序，`tradingagentspro --version` 打印版本号
- 环境变量在一处读取：新增数据库地址 `TRADINGAGENTSPRO_DATABASE_URL`，加上适配器已经定好的 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY`、`DISCORD_BOT_TOKEN`、`DISCORD_GUILD_ID`、`DISCORD_CHANNEL_ID`、`DISCORD_USER_ID`。缺少的一次全部列出；错误信息和日志里只有变量名，没有值
- Python 日志：写到 stderr（systemd 交给 journald），时间用 UTC；级别 INFO；httpx 每个请求一条的日志不输出；DBOS 的日志走同一个输出
- 启动顺序：读环境变量 → 连接并升级数据库 → 加载交易日历 → 装配 → Alpaca 账户设置 → 启动对账 → 第一次健康判定 → 启动 Discord → 启动 DBOS → 启动完成
  - 模拟盘的装配：Alpaca 两个适配器共用一个客户端，每笔佣金取风控配置的值；执行配置打开买单和止损单错开；各模块的存储用状态库的实现；风控、紧急退出、健康、行情、通知都用各自配置的默认值；行情接 OpenD
  - 启动对账有不一致：写警告，照常启动（执行模块自己暂停开仓）
  - 第一次健康判定：模型、预算、调度器三项按正常传入，券商连接按正常（启动对账刚读过券商），行情取参考股票的更新时刻，OpenD 连不上时按没有行情；处于降级模式时写警告，照常启动。OpenD 没开不算启动失败
  - 其余任何一步失败：写错误日志，按相反的顺序关闭已经打开的部分，退出码 1，由部署的自动重启再试
- DBOS：加入依赖；用状态库所在的同一个数据库（DBOS 的表在 `dbos` schema）；启动时 launch，停止时 destroy。现在还没有工作流，「下单流程」只需要注册工作流
- 「下单流程」接上之前的提交接口和按钮回调：`/plan` 查持仓的板块已经用执行模块的账户数据；提交和按钮回调一律报错「下单流程还没接上」，`/plan` 回复提交出错，按钮提示没有送达
- 停止：收到 SIGTERM 或 SIGINT 后依次关闭 DBOS、Discord、OpenD 行情连接、数据库连接；某一项出错只写日志，其余照常关闭；退出码 0
- 由人运行的冒烟运行：本机 OpenD、Alpaca 模拟盘、真实 Discord、本机 Docker 里的临时 PostgreSQL

## Non-goals

- 下单工作流、盘中巡检（行情、止损单对齐、本地备用止损、紧急退出判定）、风控和执行的锁、风控账户数据的拼装：「下单流程」「紧急退出流程」
- 启动对账有不一致时的 Discord 告警、收盘后的定时对账：「收盘对账」（add-discord-notify 已经把「启动时和收盘后的对账有不一致时推送告警」交给它）
- 进入和退出降级模式的告警、定时的健康判定、等待对账时去对账：「健康监控接线」
- systemd 单元、compose 文件、环境变量模板、备份、部署机器的准备：「部署」
- 实盘（moomoo 券商适配器、关掉错开）和回放模式的装配，以及命令行选模式：M7、M6
- 配置文件和阈值的环境变量：风控和紧急退出直接用默认值（update-risk-loosen-defaults 定的），其他模块也用默认值
- 和 moomoo 的交易日接口核对：已决定不做（临时休市过去 25 年约 6 次，漏掉的后果有限）
- 股票池：它的表 M4 才有
- 启动、停止时发 Discord 消息：只写日志；程序日志 M4 才有
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

- `bootstrap`：命令行、环境变量、日志、启动顺序和每一步失败时的处理、模拟盘的装配、交易日历的加载范围、启动对账和第一次健康判定的处理、DBOS 的启动和停止、「下单流程」接上之前的提交接口和按钮回调、停止和退出码

### Modified Capabilities

无。trading-calendar spec 写明加载范围「M2 接线时写进配置」，本变更照做，不改它的要求。

## Impact

- **代码**：改 `bootstrap/`，外加 discord 包根的一行导出
  - `cli.py`（改）：子命令 `run` 和 `--version`，安装信号处理
  - 新增 `settings.py`（环境变量）、`logs.py`（日志配置）、`startup.py`（启动对账和第一次健康判定）、`order_flow.py`（「下单流程」接上之前的提交接口和按钮回调）、`app.py`（外部连接、装配、启动顺序、DBOS、停止）
  - `database.py`、迁移不变
  - `integrations/discord/__init__.py`：包根多导出已有的协议 `Gateway`，装配从包根 import
  - modules、workflows、contracts、shared 和 integrations 的其余部分都不变
- **依赖**：新增 `dbos`（3.2 及以上）
- **测试**：`tests/bootstrap/` 下每个文件一组；装配和启动的测试用 testcontainers 的 PostgreSQL，Alpaca、OpenD、Discord 用替身，DBOS 是真的
- **文档**：README「常用命令」加 `uv run tradingagentspro run` 和它要的环境变量；`docs/实现路线.md` 的进度
- **对后续变更的影响**：
  - 「部署」：
    - 环境变量模板写上 7 个变量（bootstrap spec「环境变量」的表）。数据库地址写成 `postgresql://用户:密码@主机:端口/数据库`；这个用户要能在数据库里建表和建 schema（DBOS 建 `dbos` schema）
    - 进程：主程序 `tradingagentspro run` 一个进程；OpenD 在同一台机器的 127.0.0.1:11111（moomoo 适配器的默认值，不一样时再加变量）；PostgreSQL 在 Docker 里
    - 端口：主程序不监听任何端口（DBOS 3 没有管理端口，Alpaca 和 Discord 都是向外连）
    - 重启：启动失败退出码 1，收到 SIGTERM 正常停止退出码 0。按退出码设自动重启，重启间隔建议不短于 30 秒：每次启动都会向 Discord 重新注册 `/plan`
    - 主程序启动要等数据库可连；不用等 OpenD 登录，OpenD 没开时照常启动，盘中进入降级模式
    - 日志在 stderr，交给 journald；moomoo SDK 另外往运行用户的 `~/.com.moomoo.OpenD/Log` 写自己的日志文件（按天轮换，最多 20 个）
    - 清空状态库时 DBOS 的 `dbos` schema 一起清掉（同一个数据库）；备份也覆盖这两个 schema
  - 「下单流程」：
    - 用真正的实现替换 `bootstrap/order_flow.py` 里的 `submit` 和按钮回调；`held_sector` 本变更已经接上账户数据，不用再做
    - 工作流在 `start` 里装配完用例以后、DBOS launch 之前注册；启动时的对账在 DBOS 恢复工作流之前做完
    - 停止时 DBOS 不等工作流结束（destroy 用默认值 0）；停在 `DBOS.recv` 上等确认的工作流不能让进程退不出去，要在这个变更里确认
    - DBOS 的应用版本用默认的（按工作流代码算出来），代码改了以后旧版本没跑完的工作流不会被新代码恢复；要不要固定版本在这个变更里定
    - 按钮回调在 Discord 的工作线程里调用 `DBOS.send`（不在工作流里），add-discord-notify 的 design 留给装配时验证，由这个变更的测试验证
    - 风控和执行的锁定下来以后，决定 `held_sector` 读账户数据要不要拿锁
  - 「紧急退出流程」：盘中巡检在 `start` 里、启动完成之前开始，停止时登记到同一个关闭顺序里，排在 DBOS 之前关闭
  - 「收盘对账」：启动对账的结果在 `TradingApp.reconciliation`，有不一致时的告警在 Discord 启动以后发；收盘后的对账和告警共用一处
  - 「健康监控接线」：启动时的判定结果在健康模块的当前状态里，处于降级模式时的告警在 Discord 启动以后发；定时判定的信号拼装替换 `startup.py` 里的那一份
  - M4：启动、停止从 Python 日志改为写程序日志，并记下崩溃恢复
  - M7：装配实盘（moomoo 的两个券商适配器、错开开关关闭），命令行加选择模式

## 需要人决定

无。对应的「技术栈与部署」「故障处理」「程序日志」三节和本变更的做法一致：密钥在环境变量里；启动时先对账，DBOS 恢复的工作流在对账之后才运行，符合「执行模块恢复后先与券商对账」；启动、停止在 M2 写 Python 日志是已经定好的。
