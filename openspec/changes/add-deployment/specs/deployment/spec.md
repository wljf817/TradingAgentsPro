# Spec Delta

## Purpose

部署把交易侧放到一台常开的机器上无人值守地运行：规定这台机器上有哪些服务、按什么顺序启动、出错时怎样重启、监听哪些端口、密钥放在哪里、怎样备份和恢复状态库，以及更新、重置和本机开发时要遵守的步骤。

## ADDED Requirements

### Requirement: 部署机器和服务

交易侧 SHALL 部署在一台常开的 x86_64 Debian 机器（部署机器）上，运行下表的服务：

| 服务 | 怎样运行 | 运行身份 |
| --- | --- | --- |
| 状态库 | PostgreSQL 18，在 Docker 里，用仓库里的 compose 文件 | Docker（root） |
| OpenD | moomoo 的 Linux 命令行版 OpenD，由 systemd 管理 | 服务用户 |
| 主程序 | `tradingagentspro run`，由 systemd 管理 | 服务用户 |
| 备份 | 每天一次，由 systemd 的定时器启动 | root |

- 服务用户是一个只给本系统用的系统用户，不能用密码登录
- 主程序 SHALL 从部署机器上的仓库克隆运行，依赖 SHALL 在部署机器上按仓库的锁文件安装；MUST NOT 使用在开发用的 Mac 上构建的镜像或安装包
- 部署机器的时钟 MUST 和 NTP 同步；部署机器的时区不影响程序，不要求设置
- 所有服务 SHALL 开机自动启动
- 部署的全部步骤写在仓库的部署说明里，由人在部署机器上操作

#### Scenario: 第一次部署完成

- **GIVEN** 人按部署说明完成了第一次部署
- **WHEN** 查看部署机器上的服务
- **THEN** 状态库的容器是健康的，OpenD 和主程序在运行，备份的定时器已启用
- **AND** 主程序的日志里有「启动完成」

#### Scenario: 时钟没有同步

- **GIVEN** 部署机器的时钟没有和 NTP 同步
- **WHEN** 按部署说明检查机器的前提条件
- **THEN** 检查不通过，先打开时间同步，再继续部署

#### Scenario: 不用 Mac 上构建的东西

- **WHEN** 部署或更新主程序
- **THEN** 部署机器从 GitHub 拉取代码，按锁文件从软件源安装依赖
- **AND** 没有从 Mac 复制任何构建产物

### Requirement: 启动顺序

部署机器开机时，服务 SHALL 按下面的顺序启动：

1. Docker 启动以后，状态库启动，并等到 PostgreSQL 能在 TCP 上接受连接，最多等「等状态库就绪的上限」（默认 300 秒）
2. 状态库就绪（或者等待失败）以后，主程序启动
3. OpenD 和前两步互不等待

- 主程序 MUST NOT 等 OpenD 启动或登录：OpenD 没开时主程序照常启动，盘中进入降级模式（bootstrap spec「第一次健康判定」）
- 状态库等待失败时，主程序照样启动，按「自动重启」反复重试，直到状态库可以连接

#### Scenario: 正常开机

- **GIVEN** 部署机器关机前所有服务都在运行
- **WHEN** 部署机器开机
- **THEN** 状态库先就绪，主程序在它之后启动并写出「启动完成」
- **AND** OpenD 启动并用记住的密码登录

#### Scenario: 开机时状态库起不来

- **GIVEN** 开机时 PostgreSQL 在 300 秒内没有就绪
- **WHEN** 主程序启动
- **THEN** 主程序启动失败、退出码 1，每 30 秒重试一次
- **AND** PostgreSQL 就绪以后的下一次重试启动完成，不需要人操作

#### Scenario: 开机时 OpenD 还没登录

- **GIVEN** 主程序启动时 OpenD 还没登录完成
- **WHEN** 主程序启动
- **THEN** 主程序照常启动完成；在常规交易时段内处于降级模式，日志写明取不到行情

### Requirement: 自动重启

各服务退出时 SHALL 按下表处理：

| 服务 | 什么时候 | 怎样处理 |
| --- | --- | --- |
| 主程序 | 退出码不是 0，或者被信号杀掉（崩溃） | 等「主程序重启间隔」（默认 30 秒）后重启，不限次数 |
| 主程序 | 退出码 0（收到 SIGTERM 后正常停止） | 不重启 |
| OpenD | 任何退出 | 等「OpenD 重启间隔」（默认 30 秒）后重启，不限次数 |
| PostgreSQL | 容器退出，不是人停的 | Docker 立即重启容器 |

- 人停止主程序（`systemctl stop`）时 SHALL 发送 SIGTERM，最多等「停止等待上限」（默认 90 秒），还没退出时强制结束；人停止的服务不重启
- 主程序重启以后由 DBOS 恢复没有完成的工作流，启动时先和券商对账（bootstrap spec「启动顺序」）

#### Scenario: 启动失败以后重启

- **GIVEN** Alpaca 暂时不可用，主程序启动失败、退出码 1
- **WHEN** 过了 30 秒
- **THEN** 主程序再次启动；连续失败几十次以后照样继续重试
- **AND** Alpaca 恢复以后的下一次启动完成

#### Scenario: 崩溃以后重启

- **GIVEN** 主程序在运行
- **WHEN** 主程序被 SIGKILL 杀掉（比如内存不足）
- **THEN** 30 秒后主程序重启，启动时先对账

#### Scenario: 正常停止不重启

- **WHEN** 人运行 `systemctl stop` 停止主程序
- **THEN** 主程序收到 SIGTERM，按相反顺序关闭，退出码 0
- **AND** 主程序不会被重启

#### Scenario: 停止超时

- **GIVEN** 主程序收到 SIGTERM 以后 90 秒还没退出
- **WHEN** 停止等待到达上限
- **THEN** 主程序被强制结束，不会被重启

#### Scenario: OpenD 退出以后重启

- **GIVEN** OpenD 在运行
- **WHEN** OpenD 进程退出，不论退出码
- **THEN** 30 秒后 OpenD 重启，用记住的密码登录

#### Scenario: PostgreSQL 容器崩溃

- **GIVEN** 状态库在运行
- **WHEN** PostgreSQL 的容器意外退出
- **THEN** Docker 立即重启容器，数据卷里的数据还在

### Requirement: 监听的端口

本系统在部署机器上 SHALL 只监听下表的端口，并且 MUST 只绑定在 127.0.0.1：

| 服务 | 端口 | 用途 |
| --- | --- | --- |
| PostgreSQL | 127.0.0.1:5432 | 主程序和备份连接状态库 |
| OpenD | 127.0.0.1:11111 | 行情 API，和主程序连接 OpenD 的默认地址一致 |
| OpenD | 127.0.0.1:22222 | OpenD 的远程命令（输入验证码、要回行情权限） |

- 主程序不监听任何端口
- 从别的机器 MUST NOT 能连上这些端口；本机开发要用 OpenD 时经 SSH 隧道（见「本机开发和部署机器」）

#### Scenario: 只在本机监听

- **WHEN** 部署完成后在部署机器上列出监听的 TCP 端口
- **THEN** 本系统的端口只有上表三个，地址都是 127.0.0.1
- **AND** 没有主程序监听的端口

#### Scenario: OpenD 的地址和程序一致

- **WHEN** 主程序用 moomoo 适配器的默认配置连接 OpenD
- **THEN** 连到 OpenD 监听的 127.0.0.1:11111

#### Scenario: 从别的机器连接

- **WHEN** 从局域网里的另一台机器连接部署机器的 5432、11111 或 22222 端口
- **THEN** 连接被拒绝

### Requirement: 密钥

密钥 SHALL 按下表存放，MUST NOT 出现在仓库、systemd 单元文件和进程的命令行里：

| 密钥 | 存放在哪里 | 谁能读 |
| --- | --- | --- |
| 数据库密码、Alpaca 的 key 和 secret、Discord token | 部署机器上的环境变量文件 | 只有 root；systemd 读取后作为环境变量交给服务进程 |
| PostgreSQL 超级用户的密码 | 部署机器上 PostgreSQL 的环境变量文件 | 只有 root |
| moomoo 的登录密码 | 不写进任何文件；OpenD 第一次交互登录时记住 | OpenD（服务用户） |
| 克隆仓库用的 Deploy key | 服务用户的 SSH 目录 | 只有服务用户；在 GitHub 上只读 |

- 仓库里的环境变量模板 SHALL 只有变量名和说明，没有值
- 主程序的环境变量模板 SHALL 恰好列出主程序读取的全部环境变量（bootstrap spec「环境变量」的表），顺序相同
- 单元文件引用的每个环境变量文件，仓库里都 SHALL 有对应的模板
- moomoo 的登录账号不是密钥，放在 OpenD 的环境变量文件里，出现在 OpenD 的命令行上

#### Scenario: 模板和程序读取的变量一致

- **GIVEN** 主程序新增了一个环境变量
- **WHEN** 只改了程序，没有改主程序的环境变量模板
- **THEN** 测试失败，指出模板缺少的变量

#### Scenario: 模板里没有值

- **WHEN** 检查仓库里的三个环境变量模板
- **THEN** 每个变量的等号后面都是空的

#### Scenario: 别的用户读不到密钥

- **GIVEN** 部署完成
- **WHEN** 服务用户或者其他非 root 用户读取环境变量文件
- **THEN** 没有权限

#### Scenario: Deploy key 不能推送

- **WHEN** 在部署机器上用 Deploy key 向仓库推送
- **THEN** GitHub 拒绝推送

### Requirement: OpenD 的登录和行情权限

- OpenD 第一次在部署机器上登录时，人 SHALL 以服务用户的身份在终端里交互登录，选择记住密码；要求手机验证码时输入验证码。之后由 systemd 启动的 OpenD SHALL 用记住的密码登录，不需要人
- OpenD SHALL 在行情权限被别的设备抢走以后自动要回；自动要回失败时，人用 OpenD 的远程命令 `request_highest_quote_right` 要回
- 记住的登录失效、OpenD 登录不上时，人按部署说明重新交互登录一次
- `uv.lock` 里 moomoo-api 的版本和部署机器上 OpenD 的版本 SHALL 一致；升级一个时另一个一起升级

#### Scenario: 新设备第一次登录要验证码

- **GIVEN** 部署机器第一次登录这个 moomoo 账号
- **WHEN** 人在终端里交互登录 OpenD
- **THEN** OpenD 要求手机验证码；输入以后登录成功，人退出交互的 OpenD，再交给 systemd 启动

#### Scenario: 运行中要求验证码

- **GIVEN** systemd 管理的 OpenD 在运行
- **WHEN** OpenD 要求手机验证码
- **THEN** 人经远程命令端口先请求验证码，再输入验证码，OpenD 继续登录，不用停止服务

#### Scenario: 记住的登录失效

- **GIVEN** OpenD 记住的登录已经失效
- **WHEN** OpenD 重启
- **THEN** OpenD 登录不上，主程序照常运行，盘中行情停止更新、进入降级模式
- **AND** 人停止 OpenD 的服务，重新交互登录并选择记住密码，再启动服务，行情恢复

#### Scenario: 行情权限被手机 App 抢走

- **GIVEN** OpenD 持有最高行情权限
- **WHEN** 人在手机 App 上登录同一个账号，抢走了行情权限
- **THEN** OpenD 自动要回行情权限

#### Scenario: 10 秒内被抢走两次

- **GIVEN** 别的设备在 10 秒内抢了两次行情权限，OpenD 不再自动要回
- **WHEN** 人经远程命令端口发送 `request_highest_quote_right`
- **THEN** OpenD 要回最高行情权限

#### Scenario: moomoo-api 升级

- **GIVEN** 一次更新把 `uv.lock` 里 moomoo-api 升到新版本
- **WHEN** 人在部署机器上做这次更新
- **THEN** 先把 OpenD 升到同一个版本，再更新主程序

### Requirement: 模拟盘账户和 Discord 机器人

- Alpaca 模拟盘账户的初始资金 SHALL 等于打算用于实盘的资金（「模拟盘初始资金」，默认 2,000 美元），在第一次部署和每次重置模拟盘账户时设定
- Discord 机器人 SHALL 以 `bot` 和 `applications.commands` 两个 scope 加入服务器；在告警和确认的频道里有「查看频道」「发送消息」「嵌入链接」权限；「读取消息历史」只给冒烟测试用，程序不需要；不需要任何特权 intent。本人在这个频道里 SHALL 有「使用应用命令」权限（默认就有），`/plan` 的回复走交互，不需要别的权限
- 同一个 Discord token、同一个 Alpaca 账户同时只能有一个主程序在用：本机要运行连 Discord 或 Alpaca 的冒烟测试时，SHALL 先停止部署机器上的主程序，测完再启动

#### Scenario: 初始资金

- **WHEN** 第一次部署或重置模拟盘账户
- **THEN** 模拟盘账户的权益是 2,000 美元，没有持仓和挂单

#### Scenario: 机器人缺少授权

- **GIVEN** 机器人加入服务器时没有 `applications.commands`，或者在频道里没有「查看频道」权限
- **WHEN** 主程序启动
- **THEN** 启动 Discord 这一步失败，日志写明原因，每 30 秒重试
- **AND** 人补上授权以后的下一次重试启动完成

#### Scenario: 本机跑冒烟测试

- **GIVEN** 部署机器上的主程序在运行
- **WHEN** 人要在本机运行连 Discord 或 Alpaca 的冒烟测试
- **THEN** 先停止部署机器上的主程序，测完再启动；部署机器上的 OpenD 不用停

### Requirement: 日志

- 主程序和 OpenD 的标准输出、标准错误 SHALL 写进 journald；PostgreSQL 容器的日志也 SHALL 写进 journald
- journald SHALL 跨重启保留日志
- moomoo SDK 和 OpenD 另外写在服务用户主目录下的 `.com.moomoo.OpenD/Log`，不另做处理

#### Scenario: 查看启动日志

- **WHEN** 人在部署机器上查看主程序的日志
- **THEN** 看到每一步启动的日志和「启动完成」，时间是 UTC

#### Scenario: 重启以后还能看到之前的日志

- **GIVEN** 部署机器重启过
- **WHEN** 人查看上一次开机时主程序的日志
- **THEN** 日志还在

### Requirement: 备份

- 备份 SHALL 每天在「备份时刻」（默认 06:00 UTC，美股常规时段和盘后都已经结束）运行一次，把整个状态库（`public` 和 `dbos` 两个 schema）导出成一个可以用 `pg_restore` 恢复的文件
- 备份文件 SHALL 写在部署机器本地只有 root 能读的目录里，文件名是开始备份的 UTC 时刻
- 备份成功以后 SHALL 只保留最新的「备份保留份数」（默认 14）个备份文件，删掉更旧的
- 备份失败时 MUST NOT 删除任何旧备份，也 MUST NOT 留下会被当作备份的文件；这次运行记为失败
- 部署机器在备份时刻关着时，SHALL 在下次开机以后补做一次

#### Scenario: 每天备份

- **GIVEN** 状态库在运行
- **WHEN** 到了 06:00 UTC
- **THEN** 备份目录里多了一个以当时 UTC 时刻命名的备份文件
- **AND** 用它恢复出的数据库里有 `public` 和 `dbos` 两个 schema 的全部表

#### Scenario: 超过保留份数

- **GIVEN** 备份目录里已经有 15 个备份文件
- **WHEN** 备份成功
- **THEN** 留下最新的 14 个（包括这次的），最旧的 2 个被删掉
- **AND** 备份目录里别的文件不动

#### Scenario: 不到保留份数

- **GIVEN** 备份目录里有 3 个备份文件
- **WHEN** 备份成功
- **THEN** 4 个备份文件都在，没有删除

#### Scenario: 备份失败

- **GIVEN** PostgreSQL 没在运行
- **WHEN** 备份运行
- **THEN** 这次备份记为失败，日志里有原因
- **AND** 旧的备份文件都在，没有多出新的备份文件

#### Scenario: 错过备份时刻

- **GIVEN** 06:00 UTC 时部署机器关着
- **WHEN** 部署机器开机
- **THEN** 开机以后补做一次备份

### Requirement: 更新

更新主程序 SHALL 按下面的步骤：

1. 先手动做一次备份
2. 以服务用户的身份拉取 main 分支（只快进），按锁文件同步依赖，不装开发依赖
3. 重启主程序；数据库的迁移在主程序启动时自动运行（bootstrap spec「数据库」）

- 这次更新改了 `deploy/` 下的文件时，SHALL 在停止主程序的情况下重新安装 systemd 单元、重启状态库，再启动主程序
- 更新以后主程序启动失败时，SHALL 回到上一个提交并同步依赖；这次更新带有新的迁移时，还要用第 1 步的备份恢复状态库（「恢复备份」）

#### Scenario: 只改代码的更新

- **GIVEN** main 分支上有新的提交，没有新的迁移
- **WHEN** 人按步骤更新
- **THEN** 主程序重启，启动日志里是新的版本，启动完成

#### Scenario: 带迁移的更新

- **GIVEN** 新的提交带有一个新的迁移
- **WHEN** 人按步骤更新
- **THEN** 主程序启动时把状态库升级到新的版本，启动完成

#### Scenario: 更新以后启动失败

- **GIVEN** 更新以后主程序每次启动都失败
- **WHEN** 人回到上一个提交、同步依赖；有新迁移时再用更新前的备份恢复状态库
- **THEN** 主程序启动完成

#### Scenario: 部署文件改了

- **GIVEN** 这次更新改了 systemd 单元或 compose 文件
- **WHEN** 人按步骤更新
- **THEN** 新的单元和 compose 设置生效，主程序在状态库就绪以后启动完成

### Requirement: 恢复备份

状态库丢失或损坏时，人 SHALL 按下面的步骤恢复：停止主程序 → 删掉并重建数据库 → 用一个备份文件恢复 → 启动主程序。

- 恢复的是备份时刻的状态；之后在券商那边发生的事由启动对账报出不一致
- 备份之后挂过新止损单的股票，恢复以后再挂止损单会和券商那边的旧订单用同一个客户端订单 ID（止损单的序号退回了备份时的值），挂单被拒。模拟盘阶段遇到这种情况 SHALL 改用「全部重置」

#### Scenario: 备份之后券商那边没有变化

- **GIVEN** 状态库损坏，最新的备份之后没有成交、也没有挂新的订单
- **WHEN** 人用最新的备份恢复并启动主程序
- **THEN** 启动对账没有不一致，启动完成

#### Scenario: 备份之后有过成交

- **GIVEN** 最新的备份之后有过成交或者新挂的止损单
- **WHEN** 人用最新的备份恢复并启动主程序
- **THEN** 启动对账写出不一致，执行模块暂停开仓
- **AND** 模拟盘阶段人改用「全部重置」

### Requirement: 全部重置

要从头开始时，人 SHALL 同时重置状态库和 Alpaca 模拟盘账户：

1. 停止主程序
2. 删掉并重建数据库，`public` 和 `dbos` 两个 schema 一起清空
3. 在 Alpaca 后台重置模拟盘账户，初始资金设为「模拟盘初始资金」，生成新的 key，换进主程序的环境变量文件
4. 启动主程序

- MUST NOT 只清空状态库而不重置模拟盘账户，也 MUST NOT 只重置模拟盘账户而不清空状态库

#### Scenario: 全部重置

- **WHEN** 人按步骤全部重置
- **THEN** 主程序启动完成，启动对账没有不一致
- **AND** 模拟盘账户的权益是 2,000 美元，状态库里没有任何记录

#### Scenario: 只清空状态库

- **GIVEN** 模拟盘账户里有以前挂过的止损单，比如 `AAPL-STOP-1`
- **WHEN** 只清空状态库、不重置模拟盘账户，之后又要给 AAPL 挂止损单
- **THEN** 新的止损单用同一个客户端订单 ID，被券商拒绝；所以部署说明要求两者一起重置

#### Scenario: 只重置模拟盘账户

- **GIVEN** 状态库里记着持仓和挂着的止损单
- **WHEN** 只重置模拟盘账户、不清空状态库，然后启动主程序
- **THEN** 启动对账写出不一致，执行模块暂停开仓；所以部署说明要求两者一起重置

### Requirement: 本机开发和部署机器

- 部署机器上的 OpenD 运行以后，本机 MUST NOT 再用同一个账号运行 OpenD：它会抢走最高行情权限，让部署机器上的主程序进入降级模式
- 本机要连 OpenD（比如 moomoo 的冒烟测试）时，SHALL 经 SSH 隧道把本机的 127.0.0.1:11111 转到部署机器的 127.0.0.1:11111，代码不用改连接地址
- 本机连 Discord、Alpaca 的冒烟测试按「模拟盘账户和 Discord 机器人」先停部署机器上的主程序

#### Scenario: 本机经隧道连 OpenD

- **GIVEN** 本机没有运行 OpenD，开着到部署机器的 SSH 隧道
- **WHEN** 在本机运行 `uv run pytest -m moomoo_opend`
- **THEN** 测试连到部署机器上的 OpenD，部署机器上的 OpenD 保持最高行情权限

#### Scenario: 本机又开了 OpenD

- **GIVEN** 部署机器上的 OpenD 持有最高行情权限
- **WHEN** 人在本机打开 OpenD 并登录同一个账号
- **THEN** 两边会抢行情权限；部署说明要求本机不再运行 OpenD

### Requirement: 部署的配置项

部署 SHALL 提供下表的配置项，每项只在一个地方设定：

| 配置项 | 默认值 | 在哪里设定 |
| --- | --- | --- |
| 主程序重启间隔 | 30 秒 | 主程序的 systemd 单元 |
| OpenD 重启间隔 | 30 秒 | OpenD 的 systemd 单元 |
| 停止等待上限 | 90 秒 | 主程序的 systemd 单元 |
| 等状态库就绪的上限 | 300 秒 | 状态库的 systemd 单元 |
| 备份时刻 | 每天 06:00 UTC | 备份的 systemd 定时器 |
| 备份保留份数 | 14 | 备份脚本 |
| 模拟盘初始资金 | 2,000 美元 | Alpaca 后台（部署说明） |

- 主程序重启间隔 MUST NOT 短于 30 秒：主程序每次启动都会向 Discord 重新注册 `/plan`

#### Scenario: 改保留份数

- **GIVEN** 备份脚本里的保留份数改为 7
- **WHEN** 备份目录里已经有 7 个备份文件，备份成功
- **THEN** 留下最新的 7 个

#### Scenario: 默认值

- **WHEN** 按仓库里的部署文件部署
- **THEN** 各配置项都是上表的默认值
