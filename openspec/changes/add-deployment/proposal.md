# Proposal

## Why

「启动与装配」已经能用 `tradingagentspro run` 启动交易侧程序，但它只在开发用的 Mac 上、由人在终端里运行。M2 的完成标志要「连续 10 个交易日对账无差异」，需要一台常开机器无人值守地运行状态库、OpenD 和主程序，开机自动起来、出错自动重启、数据有备份。M1、M2 各变更也把一批部署事项留给了这一步：环境变量模板、数据库用户的权限、重启策略、OpenD 的登录和行情权限、模拟盘账户的初始资金和重置、Discord 机器人的授权、清空状态库时的注意事项。

本变更的目的只有一个：**提供把交易侧部署到一台常开的 x86 Debian 机器上所需的文件和操作步骤，让 PostgreSQL、OpenD、主程序和每天的备份在这台机器上无人值守地运行。** 实际部署由人按步骤在机器上完成。

引用的 Proposal（完整版）章节：

- 「技术栈与部署」：
  - 部署一行：一台常开机器，用 Docker 运行 PostgreSQL，另外运行 OpenD 和主程序
  - 状态库一行：PostgreSQL，同时作为 DBOS 的状态存储
  - 券商一行：moomoo OpenAPI + OpenD 网关提供行情；模拟盘用 Alpaca
  - 通知与人工确认一行：Discord 机器人，只接受指定用户的操作
  - 可靠性要求：API key 和 Discord token 存环境变量；进程崩溃后由 DBOS 从断点恢复，执行模块恢复后先与券商对账

## What Changes

- 仓库新增 `deploy/` 目录，放部署要用的全部文件：
  - PostgreSQL 18 的 compose 文件：只在 127.0.0.1 上发布端口，数据放在命名卷里，密码从机器上的环境变量文件读
  - systemd 单元：状态库（等 PostgreSQL 能接受连接）、OpenD、主程序、备份和它的定时器
  - 三个环境变量模板（主程序、PostgreSQL、OpenD 的登录账号），只有变量名和说明，没有值；主程序的模板和 `bootstrap/settings.py` 的 `VARIABLES` 一致，由测试保证
  - 备份脚本：每天用 `pg_dump` 备份整个状态库（`public` 和 `dbos` 两个 schema）到本机磁盘，保留最新的若干份
  - 部署说明 `deploy/README.md`：第一次部署的完整步骤，以及更新、停止、恢复备份、全部重置、OpenD 重新登录、要回行情权限的步骤
- 机器上的做法：
  - 一个专用的服务用户运行 OpenD 和主程序；Docker 和备份由 root 运行
  - 代码用只读的 Deploy key 从 GitHub 克隆，按锁文件安装依赖；不使用在 Mac 上构建的任何镜像或安装包
  - 密钥只放在机器上只有 root 能读的环境变量文件里，由 systemd 读取后交给服务进程；OpenD 的密码不写进任何文件，由 OpenD 第一次交互登录时记住
  - 主程序：启动失败（退出码 1）或崩溃时 30 秒后自动重启、不限次数；收到 SIGTERM 正常退出（退出码 0）时不重启；开机时等状态库能接受连接再启动，不等 OpenD
  - OpenD：命令行版，只在 127.0.0.1 监听 API 和远程命令；被别的设备抢走行情权限时自动要回
  - 日志都进 journald，并且跨重启保留
- 测试：解析部署文件，检查环境变量模板、单元的重启设置和环境变量文件、compose 只绑定 127.0.0.1、OpenD 的地址和代码默认值一致；用假的 `docker` 运行备份脚本，检查保留份数和失败时不删旧备份
- 开发依赖加 PyYAML 和它的类型标注（只给测试解析 compose 文件用）
- README 加部署说明的链接；部署以后，本机连 OpenD 的冒烟测试经 SSH 隧道连部署机器上的 OpenD

## Non-goals

- 对 `src/` 的任何修改：主程序的环境变量、日志、退出码和启动顺序都已经满足部署的需要
- 监控面板、远程收集日志、备份到别的机器、备份失败的告警：模拟盘阶段机器本地的备份和 journald 足够；告警由「健康监控接线」统一考虑，见「对后续变更的影响」
- 多台机器、主备切换；把主程序或 OpenD 放进容器（见 design D1）
- 主机的加固（防火墙、systemd 沙箱选项、自动安全更新）：本系统不对外监听任何端口
- 实盘：M7（交易密码、off-host 备份到时再定）
- moomoo 行情的盘前冒烟测试（add-moomoo-quotes 任务 4.2）：留在那个变更，由人以后运行；本变更只写明部署以后怎样经隧道运行它
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

- `deployment`：部署机器上的服务和启动顺序、自动重启、监听的端口、密钥和文件权限、OpenD 的登录和行情权限、模拟盘账户和 Discord 机器人、日志、备份和保留、更新和回滚、恢复备份、全部重置、本机开发和部署机器共用账户时的约定

### Modified Capabilities

无。主程序的行为不变；bootstrap spec 的退出码、环境变量和启动顺序原样作为部署的前提。

## Impact

- **新文件**：
  - `deploy/README.md`（部署说明）、`deploy/compose.yaml`、`deploy/backup.sh`
  - `deploy/tradingagentspro.env.example`、`deploy/postgres.env.example`、`deploy/opend.env.example`
  - `deploy/systemd/`：`tradingagentspro.service`、`tradingagentspro-db.service`、`tradingagentspro-opend.service`、`tradingagentspro-backup.service`、`tradingagentspro-backup.timer`
  - `tests/deploy/`：`test_env_templates.py`、`test_compose.py`、`test_units.py`、`test_backup.py`
- **代码**：无（`src/` 不变）
- **依赖**：开发依赖加 `pyyaml`（已经随 dbos 装着）和 `types-pyyaml`
- **文档**：README 加部署说明的链接和隧道的用法；`docs/实现路线.md` 的进度
- **机器**：由人按部署说明在部署机器上完成（tasks 的最后一组）
- **对后续变更的影响**：
  - 「下单流程」「紧急退出流程」：停止时 systemd 最多等 90 秒，之后强制结束；新增的工作线程和巡检要在这之内停下
  - 「健康监控接线」：OpenD 记住的登录失效、行情权限被抢走以后，表现为行情停止更新，由数据延迟检查进入降级并告警；部署说明里有恢复步骤，告警文字可以指向它。备份失败现在只在 systemd 里记为失败，是否加进告警由这个变更决定
  - 以后新增的环境变量（比如 M5 的模型 API key）：加进 `VARIABLES` 的同时要加进 `deploy/tradingagentspro.env.example`，否则 `tests/deploy/` 的测试失败；部署机器上的环境变量文件由人补上
  - M4：程序日志、案件记录写进状态库以后，备份自动覆盖（备份的是整个数据库）
  - M7：实盘交易经同一个 OpenD 下单，要解锁交易，交易密码怎样保存到时再定；实盘是否要备份到别的机器也到时再定
  - add-moomoo-quotes 任务 4.2（开盘前的冒烟测试）、add-bootstrap-wiring 任务 8.3（冒烟运行）：部署以后在本机运行时，本机不再开 OpenD，经 SSH 隧道连部署机器上的 OpenD；用同一个 Discord token 或 Alpaca 账户的，先停部署机器上的主程序

## 需要人决定

无。对应的「技术栈与部署」一节和本变更的做法一致：PostgreSQL 在 Docker 里，OpenD 和主程序直接运行在同一台常开机器上；API key 和 Discord token 由 systemd 从环境变量文件交给主程序，仍然是环境变量；自动重启加上启动时先对账，满足「进程崩溃后由 DBOS 从断点恢复，执行模块恢复后先与券商对账」。OpenD 的登录密码由 OpenD 自己记住，不经过本系统，和这一节不冲突。
