# Design

## Context

现状：

- `tradingagentspro run`（add-bootstrap-wiring）：环境变量只有 `bootstrap/settings.py` 的 `VARIABLES` 里那 7 个；日志写 stderr，时间用 UTC；启动失败退出码 1，收到 SIGTERM 或 SIGINT 正常停止退出码 0；不监听任何端口；连不上数据库就启动失败，不在进程里等待或重试；OpenD 没开时照常启动
- moomoo 适配器默认连 127.0.0.1:11111（`MoomooConfig`）；`uv.lock` 里 moomoo-api 是 10.11.7108，和用户 Mac 上的 OpenD 版本一致
- 状态库和 DBOS 用同一个数据库：本系统的表在 `public`，DBOS 的表在 `dbos`；DBOS launch 时自己建 `dbos` schema，并先连 `postgres` 库检查系统数据库在不在
- 测试用的 PostgreSQL 是 `postgres:18`（testcontainers）；CI 在 ubuntu x86 上跑，不能访问任何外部账户
- 仓库是 GitHub 上的私有仓库 `wljf817/TradingAgentsPro`；仓库里还没有任何部署文件
- 部署机器是一台常开的 x86_64 Debian 机器，上面的操作都由人按部署说明完成

外部系统（2026-10-08 查阅）：

- moomoo OpenAPI 文档的「命令行 OpenD」「运维命令」和 OpenD 的常见问题：
  - Linux 只提供 Ubuntu 和 CentOS 的构建，没有单独的 Debian 版；OpenD 所在目录要有配置文件 `OpenD.xml` 和 `Appdata.dat`，缺一个都起不来
  - 从 10.10 起，直接启动 OpenD 进入交互登录：问账号、密码和是否记住密码。记住并登录成功以后，可以用 `-login_account=账号 -login_by_remember=1` 直接登录。文档的启动参数和配置项里都不再有 `login_pwd`、`login_pwd_md5`（只有运维命令 `relogin` 还接受它们）
  - 启动参数用 `-key=value`，覆盖配置文件：`api_ip`、`api_port`、`auto_hold_quote_right`（被抢走行情权限后自动要回，默认自动；10 秒内又被抢走就不再要）、`console`（默认显示控制台）、`no_monitor`（默认启动守护进程，`1` 不启动）
  - 远程命令的地址和端口只能在配置文件里设：`telnet_ip`（默认 127.0.0.1）、`telnet_port`（不设就没有远程命令）
  - 运维命令可以在 OpenD 的控制台或经 Telnet 发送：`req_phone_verify_code`（打开设备锁的账号在新设备第一次登录时要手机验证码）、`input_phone_verify_code -code=123456`、`request_highest_quote_right`、`relogin`、`exit`
  - 常见问题：Linux 下后台运行用 `nohup ./OpenD &`；设备 ID 存在 `Device.dat`，删掉或损坏会要求重新验证设备；记住的登录靠本地保存的 token，长时间不用会过期；日志在 `~/.com.moomoo.OpenD/Log`；一个账号最多同时登录 10 个 OpenD，只有一个持有最高行情权限
- Docker 的 postgres 镜像：从 18 起 `PGDATA` 是 `/var/lib/postgresql/18/docker`，数据卷挂在 `/var/lib/postgresql`（17 及以前是 `/var/lib/postgresql/data`）；`POSTGRES_USER` 是超级用户；`POSTGRES_*` 只在数据目录为空、第一次初始化时生效
- Debian 12、13 软件源里的 PostgreSQL 客户端是 15、17，`pg_dump` 不能导出更高大版本的服务器
- Alpaca（社区论坛，没有找到官方文档）：重置模拟盘账户相当于新开一个账户，旧的 key 不再可用，要生成新的

要求见 specs/deployment/spec.md，动机见 proposal.md 的 Why。

### 模块边界

不涉及任何模块，`src/` 不变，没有模块之间传递的 Pydantic 模型。新增的文件：

```
deploy/
  README.md                        部署说明（第一次部署、日常检查、更新、停止、恢复、重置、OpenD）
  compose.yaml                     PostgreSQL 18
  backup.sh                        备份脚本（可执行）
  tradingagentspro.env.example     主程序的环境变量模板：VARIABLES 里的 7 个
  postgres.env.example             POSTGRES_PASSWORD
  opend.env.example                MOOMOO_LOGIN_ACCOUNT
  systemd/
    tradingagentspro.service       主程序
    tradingagentspro-db.service    状态库：启动容器并等到健康
    tradingagentspro-opend.service OpenD
    tradingagentspro-backup.service
    tradingagentspro-backup.timer

tests/deploy/                      部署文件的测试（deploy/ 在仓库根目录，不在 src/ 里，测试按仓库的结构放）
  __init__.py
  files.py                         读部署文件的小工具：单元文件、环境变量模板的路径和解析
  test_env_templates.py
  test_compose.py
  test_units.py
  test_backup.py
```

测试 import `tradingagentspro.bootstrap.settings.VARIABLES` 和 `tradingagentspro.integrations.moomoo.MoomooConfig`，把部署文件和代码的约定连起来。

## Goals / Non-Goals

**Goals:**

- 部署机器重启、主程序崩溃、启动时依赖的服务暂时不可用，都能自己恢复，不需要人
- 按退出码重启：启动失败和崩溃重启，人停止的不重启
- 密钥只在部署机器上，仓库里只有变量名
- 部署说明的每一步都有一条能看到结果的检查命令
- 能确定的约定（变量清单、重启设置、端口、OpenD 地址、备份的保留和失败处理）由测试在 CI 里检查

**Non-Goals:**

按「编程风格」省略的情况：

- 磁盘写满：备份 14 份的状态库在模拟盘阶段只有几十 MB，journald 自己限制大小
- 备份失败的告警：由日常检查（`systemctl --failed`）发现；要不要进告警由「健康监控接线」决定
- 备份到别的机器：模拟盘阶段丢了可以全部重置；M7 再定
- systemd 的沙箱选项、防火墙、自动安全更新：本系统只在 127.0.0.1 上监听
- Docker 升级时重启 dockerd、PostgreSQL 短暂不可用：Debian 的自动更新默认不更新 Docker 的软件源
- PostgreSQL 小版本的自动更新：部署说明里有手动更新的一条命令
- OpenD 自己的日志文件的轮换：OpenD 自己管理，文件在服务用户的主目录下
- OpenD 用记住的密码反复登录失败、被要求图形验证码：记住的登录失效时由人重新交互登录
- 用手机号（而不是 moomoo ID）登录 OpenD 时要加 `-area_code`：部署说明写明用 moomoo ID
- 机器的内存和 CPU 要求：PostgreSQL、OpenD 和一个 Python 进程，任何常见的 x86 机器都够
- PostgreSQL 的参数调优：默认值对这个数据量足够
- 更新时锁文件和代码不一致（`uv sync --locked` 失败）：CI 用同样的命令检查过 main 上的锁文件
- 恢复的备份之后又新建了表（更新的迁移）：恢复前先删掉整个数据库再建，不会留下旧表
- 同一个账号的 OpenD 在本机和部署机器上同时运行：部署说明要求本机不再运行 OpenD

## Decisions

### D1 systemd 管理 OpenD 和主程序，只有 PostgreSQL 放进 Docker

Proposal 的部署一行是「用 Docker 运行 PostgreSQL，另外运行 OpenD 和主程序」。OpenD 和主程序直接运行在机器上，由 systemd 管理；PostgreSQL 用 compose 文件运行在 Docker 里。

- systemd 直接按退出码决定重启（`Restart=on-failure`），和 bootstrap 定的退出码一一对应；`systemctl stop` 发 SIGTERM，正好是主程序正常停止的信号
- 日志：stdout 和 stderr 直接进 journald，和 bootstrap「日志交给 journald」的约定一致
- OpenD 第一次登录要交互输入密码和验证码，记住的登录和 `Device.dat` 要留在机器上：直接运行在服务用户下最简单
- 主程序放进容器要在部署机器上构建镜像、把 OpenD 的地址和 moomoo SDK 的日志目录映射进去，换不来什么

考虑过：

- 全部放进 compose：主程序要构建镜像，OpenD 要容器里的交互登录和持久化的主目录，第一次登录和找回行情权限都更麻烦
- supervisord 之类的进程管理器：多装一个东西，Debian 本来就有 systemd

### D2 部署机器上的布局

| 路径 | 是什么 | 所有者和权限 |
| --- | --- | --- |
| `/opt/tradingagentspro` | 服务用户 `tradingagentspro` 的主目录 | 服务用户 |
| `/opt/tradingagentspro/app` | 仓库的克隆，`.venv` 在里面 | 服务用户 |
| `/opt/tradingagentspro/opend` | 命令行版 OpenD 的目录（`OpenD`、`OpenD.xml`、`Appdata.dat`） | 服务用户 |
| `/opt/tradingagentspro/.ssh/id_ed25519` | Deploy key | 服务用户，0600 |
| `/opt/tradingagentspro/.com.moomoo.OpenD/` | OpenD 和 moomoo SDK 的数据和日志（程序自己建） | 服务用户 |
| `/etc/tradingagentspro/` | 三个环境变量文件 | root，目录 0700，文件 0600 |
| `/var/backups/tradingagentspro/` | 备份文件 | root，0700 |
| `/etc/systemd/system/tradingagentspro*.{service,timer}` | 从 `deploy/systemd/` 复制过去的单元 | root，0644 |
| Docker 命名卷 `tradingagentspro_pgdata` | PostgreSQL 的数据 | Docker |

- 服务用户：`adduser --system --group --home /opt/tradingagentspro --shell /bin/bash tradingagentspro`。系统用户没有密码，人用 `sudo -iu tradingagentspro` 切过去拉代码、第一次登录 OpenD；需要 shell 才能这样切换
- 服务用户不加入 `docker` 组：`docker` 组等于 root。compose 和备份由 root 运行
- 单元文件复制而不是链接到仓库：`/etc/systemd/system` 下的文件只有 root 能改；更新时 `deploy/systemd/` 变了就重新复制
- 所有路径写死在单元文件和部署说明里，不做成变量：只有一台机器

### D3 代码怎样到部署机器

- 服务用户生成一对 ed25519 密钥，公钥由人在 GitHub 仓库的 Deploy keys 里添加，不勾选写权限；第一次连接 GitHub 时按 GitHub 文档核对主机指纹
- `git clone git@github.com:wljf817/TradingAgentsPro.git /opt/tradingagentspro/app`；更新是 `git pull --ff-only`
- 依赖：`uv sync --locked --no-dev`。`--locked` 按锁文件安装、校验哈希，锁文件和 `pyproject.toml` 不一致时报错而不是改锁文件；`--no-dev` 不装测试工具。Python 3.13 由 uv 按 `.python-version` 找，系统没有时下载到服务用户的主目录
- uv 用官方安装脚本装到 `/usr/local/bin`（root 安装，所有用户可用）
- 主程序的 `ExecStart` 直接运行 `.venv/bin/tradingagentspro run`，不用 `uv run`：`uv run` 每次启动都会先同步环境，可能联网，也可能在启动时改动环境

考虑过：

- CI 构建 wheel 或镜像再下载：私有仓库要给部署机器发 token 或镜像仓库的凭据；构建产物还要另外管版本。部署机器自己按锁文件安装更简单，结果一样确定
- HTTPS 加个人访问令牌克隆：令牌的权限比只读的 Deploy key 大，还会过期

### D4 状态库：compose 文件和等待就绪的单元

`deploy/compose.yaml`：

```yaml
name: tradingagentspro

services:
  postgres:
    image: postgres:18
    restart: unless-stopped
    env_file: /etc/tradingagentspro/postgres.env
    environment:
      POSTGRES_USER: tradingagentspro
      POSTGRES_DB: tradingagentspro
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql
    healthcheck:
      test:
        - CMD
        - pg_isready
        - --host=127.0.0.1
        - --username=tradingagentspro
        - --dbname=tradingagentspro
      interval: 5s
      timeout: 5s
      retries: 12
    logging:
      driver: journald

volumes:
  pgdata:
```

- 项目名固定为 `tradingagentspro`：数据卷的名字（`tradingagentspro_pgdata`）不随目录名变
- 端口只发布到 127.0.0.1：主程序和备份都在本机；Docker 发布的端口会绕过主机的防火墙规则，所以必须在这里限定地址
- 数据卷挂在 `/var/lib/postgresql`：18 的镜像改了数据目录的位置，挂在旧路径上数据不在卷里
- 用户：直接用镜像建的 `tradingagentspro`（超级用户）。镜像的 `pg_hba.conf` 对容器里的 Unix socket 是 trust，经发布的端口从宿主机连接要密码（scram-sha-256，本机验证过）。它能建表、建 `dbos` schema，也能连 `postgres` 库（DBOS 的检查要用）。再建一个普通用户要多写初始化脚本和授权，数据库只在本机可达，省掉
- 密码只放在 `/etc/tradingagentspro/postgres.env`，compose 文件里没有。用 `openssl rand -hex 24` 生成：只有 0-9a-f，写进数据库地址不用转义。它只在第一次初始化数据卷时生效
- 健康检查用 TCP（`--host=127.0.0.1`）：第一次初始化时镜像先起一个只听 Unix socket 的临时服务器，用 socket 检查会在初始化完成之前就报健康
- `restart: unless-stopped`：容器崩溃由 Docker 立即重启；人用 `systemctl stop` 停下的不重启
- 日志用 journald 驱动：和其他服务的日志在一处，大小由 journald 限制；`docker compose logs` 照样能看。本机的 Docker（OrbStack）没有 journald，用这个驱动的容器起不来，所以本机验证 compose 时换成 json-file；部署机器上由 tasks 8.6 验证
- `postgres:18` 跟随 18 的小版本；只有人运行 `docker compose pull` 时才会更新

`deploy/systemd/tradingagentspro-db.service`：

```ini
[Unit]
Description=TradingAgentsPro 状态库（Docker 里的 PostgreSQL 18）
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/tradingagentspro/app/deploy
ExecStart=/usr/bin/docker compose up --wait
ExecStop=/usr/bin/docker compose stop
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

- `up --wait`：容器启动以后等到健康检查通过才返回，主程序排在它后面，开机时就不会因为 PostgreSQL 还没起来而先失败一次
- `TimeoutStartSec=300` 是「等状态库就绪的上限」：第一次拉镜像在部署说明里单独做，这里只是兜住 Docker 本身启动慢

考虑过：

- 不要这个单元，只靠 Docker 的重启策略在开机时拉起容器，主程序失败重启直到连上：开机时几乎每次都先有一条「启动失败」，日志里分不清真故障
- 主程序用 `Requires=` 依赖这个单元：等待失败时 systemd 不会启动主程序，也不会再试，要人处理；用 `Wants=` 时主程序照样启动、失败了按自己的重启策略一直试，PostgreSQL 起来以后自己恢复

### D5 主程序的单元

`deploy/systemd/tradingagentspro.service`：

```ini
[Unit]
Description=TradingAgentsPro 交易侧程序（模拟盘）
Wants=network-online.target tradingagentspro-db.service
After=network-online.target tradingagentspro-db.service
# 启动失败时一直重试，不因次数多而放弃
StartLimitIntervalSec=0

[Service]
Type=exec
User=tradingagentspro
Group=tradingagentspro
WorkingDirectory=/opt/tradingagentspro/app
EnvironmentFile=/etc/tradingagentspro/tradingagentspro.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/tradingagentspro/app/.venv/bin/tradingagentspro run
Restart=on-failure
RestartSec=30
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
```

- `Restart=on-failure` 对应 bootstrap 的退出码：退出码 1（启动失败）、被信号杀掉（崩溃、内存不足）都重启；退出码 0（收到 SIGTERM 后正常停止）不重启。不设 `SuccessExitStatus`、`RestartPreventExitStatus`、`RestartForceExitStatus`，免得改变这个对应关系
- `RestartSec=30`：bootstrap 的建议，每次启动都会向 Discord 重新注册 `/plan`
- `StartLimitIntervalSec=0`：systemd 默认 10 秒内启动 5 次就放弃；30 秒的间隔本来碰不到这个限制，写明不设上限，免得以后改短间隔时悄悄生效
- `Type=exec`：可执行文件不存在（比如 `.venv` 还没建）时 `systemctl start` 直接报错，而不是看起来启动成功
- `TimeoutStopSec=90`：「停止等待上限」，和 systemd 的默认值一样，写出来让它只在一处设定；bootstrap 的停止在每一步的超时之内完成
- 不排在 OpenD 之后，也不 `Wants` 它：bootstrap 要求不等 OpenD
- `PYTHONUNBUFFERED=1`：moomoo SDK 往 stdout 写日志，stdout 接的不是终端时 Python 会按块缓冲，journald 里的先后会乱
- 密钥由 systemd（root）读 `EnvironmentFile` 后放进进程的环境变量，服务用户读不到这个文件

### D6 OpenD

`deploy/systemd/tradingagentspro-opend.service`：

```ini
[Unit]
Description=moomoo OpenD（行情网关）
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=exec
User=tradingagentspro
Group=tradingagentspro
WorkingDirectory=/opt/tradingagentspro/opend
EnvironmentFile=/etc/tradingagentspro/opend.env
ExecStart=/opt/tradingagentspro/opend/OpenD -login_account=${MOOMOO_LOGIN_ACCOUNT} -login_by_remember=1 -api_ip=127.0.0.1 -api_port=11111 -auto_hold_quote_right=1 -no_monitor=1
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

- 登录：10.10 以后的 OpenD 不再从配置文件读密码（Context）。第一次由人以服务用户的身份在终端里运行 `./OpenD` 交互登录，选择记住密码，要求验证码时输入；然后输入 `exit` 退出，再交给 systemd。systemd 用 `-login_account` 和 `-login_by_remember=1` 登录。密码不进本系统的任何文件
  - 10.10 以前可以把账号和 MD5 密码写进配置文件，现在的文档已经没有这两项，所以改为记住密码
- 账号放在 `/etc/tradingagentspro/opend.env` 的 `MOOMOO_LOGIN_ACCOUNT`，单元里用 `${...}` 展开。账号不是密钥，但单元文件在仓库里，不能写具体账号
- API 地址和端口写在命令行上（`-api_ip=127.0.0.1 -api_port=11111`），不依赖 `OpenD.xml` 里的值；测试检查它们和 `MoomooConfig` 的默认值一致
- 远程命令端口只能在配置文件里设：部署说明让人在 OpenD 自带的 `OpenD.xml` 里把 `telnet_port` 设为 22222，`telnet_ip` 保持默认的 127.0.0.1。仓库里不放 `OpenD.xml` 的模板：文件格式没有文字版的文档（只有截图），自己写一份可能让 OpenD 起不来；自带的文件里其余设置都是默认值，正好是要的
- `-auto_hold_quote_right=1`：默认就是自动要回，写出来是为了让测试和读单元的人看到这个前提
- `-no_monitor=1`：不启动 OpenD 自己的守护进程，只由 systemd 负责重启，免得两个重启的机制互相干扰
- 控制台用默认值（显示）：常见问题里后台运行的做法是 `nohup ./OpenD &`，说明控制台模式在没有终端输入时能运行。`-console=0` 在 Linux 上是否会转入后台（让 systemd 以为进程退出了）文档没有写，不用
- `Restart=always`：OpenD 各种退出的退出码没有文档，不管怎样退出都重启
- 远程命令用 `telnet 127.0.0.1 22222`，输入命令、回车、看回复，`Ctrl-]` 再 `quit` 退出
- 升级 OpenD：停服务 → 把旧目录改名 → 解压新版本到 `opend/` → 在新的 `OpenD.xml` 里再设一次 `telnet_port` → 旧目录里有 `Device.dat` 时复制过来（免得重新验证设备）→ 启动 → 确认登录成功后删掉旧目录。和 `uv.lock` 里 moomoo-api 的版本一起升级

### D7 密钥文件

| 文件 | 内容 | 谁读 |
| --- | --- | --- |
| `/etc/tradingagentspro/tradingagentspro.env` | `VARIABLES` 的 7 个变量 | systemd，交给主程序 |
| `/etc/tradingagentspro/postgres.env` | `POSTGRES_PASSWORD` | `docker compose`（root） |
| `/etc/tradingagentspro/opend.env` | `MOOMOO_LOGIN_ACCOUNT` | systemd，交给 OpenD 的命令行 |

- 三个文件都是 root 所有、0600，目录 0700。读它们的都是 root（systemd 在切换到服务用户之前读 `EnvironmentFile`，`docker compose` 以 root 运行），服务用户不需要读文件本身，只在自己的进程里拿到值。这比「只有服务用户能读」更严：主程序被攻破也读不到 PostgreSQL 的密码文件（虽然数据库地址里有同一个密码）
- 仓库里的模板是 `deploy/<文件名>.example`，只有变量名和注释；人复制到 `/etc/tradingagentspro/<文件名>` 再填值。`.gitignore` 忽略 `.env` 和 `.env.*`，模板的名字不以 `.env` 开头，不受影响
- 主程序的模板照 `VARIABLES` 的顺序写，每个变量前面一行注释说明格式和从哪里拿；数据库地址的注释写明 `postgresql://tradingagentspro:<密码>@127.0.0.1:5432/tradingagentspro`
- systemd 的环境变量文件不需要引号，值里没有空格；Alpaca 的 key、Discord 的 token、十六进制的密码都满足
- 数据库密码同时出现在 `postgres.env` 和主程序的数据库地址里：一个给 PostgreSQL 初始化用，一个给主程序连接用，部署说明让人一次生成、填进两处

考虑过：一个环境变量文件给 compose 和主程序共用。主程序的文件要恰好是 `VARIABLES`（测试保证），再放 PostgreSQL 的变量就破坏了这个约定

### D8 备份

`deploy/backup.sh`（POSIX sh，root 运行）：

```sh
#!/bin/sh
# 备份整个状态库（public 和 dbos 两个 schema），写到 $1；成功以后只留最新的 KEEP 份。
set -eu

KEEP=14 # 备份保留份数
dir=$1

cd "$(dirname "$0")" # compose.yaml 所在的目录
install -d -m 700 "$dir"
docker compose exec -T postgres \
    pg_dump --username=tradingagentspro --dbname=tradingagentspro --format=custom \
    >"$dir/incomplete"
mv "$dir/incomplete" "$dir/$(date -u +%Y%m%dT%H%M%SZ).dump"

# 文件名是 UTC 时刻，按名字排序就是按时间排序，最旧的在前
set -- "$dir"/*.dump
while [ "$#" -gt "$KEEP" ]; do
    rm -- "$1"
    shift
done
```

- `pg_dump` 在容器里运行：版本和服务器一致；Debian 的 PostgreSQL 客户端版本更低，导不了 18。容器里经 Unix socket 连接不用密码（镜像的默认设置）
- 不加 `--schema`：导出整个数据库，`public` 和 `dbos` 都在里面
- custom 格式：压缩，能用 `pg_restore` 恢复
- 先写到 `incomplete`，成功后才改名为 `<UTC 时刻>.dump`：失败时 `set -e` 直接退出，留下的 `incomplete` 不是 `.dump`，不会被当作备份、也不参与保留的计数，下次备份覆盖它
- 失败时不会走到删除旧备份那一步
- 保留按文件名排序，不按修改时间：同一秒里建的文件（测试）也排得出先后
- 备份目录作为参数传入：单元传 `/var/backups/tradingagentspro`，测试传临时目录

`tradingagentspro-backup.service` 是 `Type=oneshot`，`ExecStart=/opt/tradingagentspro/app/deploy/backup.sh /var/backups/tradingagentspro`，不设 `User`（root），没有 `[Install]`：只由定时器启动。`tradingagentspro-backup.timer`：

```ini
[Timer]
OnCalendar=*-*-* 06:00:00 UTC
Persistent=true
```

- 06:00 UTC 在美东的凌晨 1 点或 2 点，常规时段和盘后都已结束；写明 UTC，不受部署机器时区影响
- `Persistent=true`：机器在那个时刻关着时，开机后补做
- 备份失败时服务记为失败，`systemctl --failed` 能看到；部署说明的「日常检查」看它和最新备份的时刻
- 部署说明写一次「恢复演练」：把最新的备份恢复到一个临时数据库，确认两个 schema 都在，再删掉

### D9 更新和回滚

部署说明里的更新步骤：

1. `sudo systemctl start tradingagentspro-backup`，确认多了一个备份
2. 以服务用户：`git -C ~/app pull --ff-only`，`git -C ~/app diff --stat HEAD@{1} -- deploy uv.lock` 看这次改了什么
3. `uv.lock` 里 moomoo-api 的版本变了：先升级 OpenD（D6）
4. 以服务用户：`cd ~/app && uv sync --locked --no-dev`
5. `deploy/` 没变：`sudo systemctl restart tradingagentspro`
6. `deploy/` 变了：停主程序 → 重新复制单元、`daemon-reload` → `sudo systemctl restart tradingagentspro-db` → 启动主程序
7. `git log -1` 和 GitHub 上 main 的最新提交一样，`journalctl -u tradingagentspro` 里有这次启动的「启动完成」（日志里的版本号是 `pyproject.toml` 的版本，不随提交变，不能用来判断）

回滚：`git -C ~/app checkout <上一个提交>`、`uv sync --locked --no-dev`、重启；这次更新带有新的迁移时，旧代码不认识新的迁移版本、启动会失败，要先用第 1 步的备份恢复（D10）。回滚以后人修好 main 再更新回 main 分支（`git checkout main`）。

- 每次更新前都备份，而不是只在有迁移时：省掉判断，备份只要几秒
- 迁移由主程序启动时自动运行（bootstrap），更新步骤里没有单独的迁移命令

### D10 恢复备份和全部重置

两者都先停主程序，再删掉并重建数据库（以 root 在 `deploy/` 目录下）：

```sh
docker compose exec -T postgres dropdb --username=tradingagentspro --force tradingagentspro
docker compose exec -T postgres createdb --username=tradingagentspro tradingagentspro
```

- `dropdb` 连默认的 `postgres` 库去删，`--force` 断开残留的连接；`public` 和 `dbos` 两个 schema 一起删掉。比删 schema 更彻底：不会漏掉以后新建的 schema
- 恢复：`sh -c 'docker compose exec -T postgres pg_restore --username=tradingagentspro --dbname=tradingagentspro < /var/backups/tradingagentspro/<文件>.dump'`。整条命令放在 root 的 `sh -c` 里：备份目录只有 root 能读，重定向要由 root 的 shell 做
- 恢复以后启动主程序，看启动对账：没有不一致就完成；有不一致或者之后挂止损单被拒（客户端订单 ID 重复，spec「恢复备份」），模拟盘阶段改为全部重置
- 全部重置：重建数据库以后，在 Alpaca 后台重置模拟盘账户、初始资金设为 2,000 美元、生成新 key，换进 `/etc/tradingagentspro/tradingagentspro.env`，再启动主程序，确认启动对账没有不一致

考虑过：`docker compose down -v` 删掉整个数据卷。也会删掉 PostgreSQL 的用户和密码，第一次初始化又要跑一遍，效果一样但更慢；恢复时也一样要重建

### D11 本机开发和部署机器

- 部署以后本机不再开 OpenD。本机要连 OpenD 时，在 Mac 上开 `ssh -N -L 11111:127.0.0.1:11111 <部署机器>`，本机的 127.0.0.1:11111 就转到部署机器的 OpenD；代码不用加 OpenD 地址的环境变量，冒烟测试的命令不变
- 本机跑连 Discord 或 Alpaca 的冒烟测试（包括 bootstrap 的冒烟运行）之前，`sudo systemctl stop tradingagentspro`，测完 `sudo systemctl start tradingagentspro`。部署机器的 OpenD 不用停
- 行情的快照限频（每 30 秒 60 次）是按账号算的，经隧道和在部署机器上一样

### D12 部署说明

放在 `deploy/README.md`：`docs/` 只记决定，部署的步骤会随部署文件一起改。README 的「常用命令」加一行链接，moomoo 冒烟测试的说明加上隧道。

章节（每一步都有一条检查命令和预期的结果）：

1. 部署机器的前提：x86_64 的 Debian 12 或 13；时间同步（`timedatectl` 显示已同步）；按 Docker 官方文档装 Docker Engine 和 compose 插件；`git`、`telnet`；uv 装到 `/usr/local/bin`；journald 持久保存（Debian 12 要建 `/var/log/journal`）
2. 服务用户和 Deploy key、克隆、`uv sync`
3. Alpaca 模拟盘重置为 2,000 美元、生成新 key（并说明为什么必须和状态库一起重置）；Discord 机器人：确认 scope、机器人在频道里的权限、不需要特权 intent，以及本人在频道里有「使用应用命令」权限（默认就有）。放在环境变量文件之前：新 key 要填进去
4. 环境变量文件：建 `/etc/tradingagentspro`，复制三个模板，生成数据库密码，填值，设权限
5. OpenD：下载 Linux 版命令行 OpenD（和 `uv.lock` 的 moomoo-api 同一版本），放到 `~/opend`，在 `OpenD.xml` 设 `telnet_port`，第一次交互登录（记住密码、验证码）。放在安装单元之前：`systemd-analyze verify` 要求 OpenD 的可执行文件已经在
6. 安装单元（复制、`daemon-reload`、`systemd-analyze verify`）
7. 状态库：`docker compose config --quiet`、`docker compose pull`、启用并启动 `tradingagentspro-db`，确认健康、只在 127.0.0.1 监听
8. 启动 OpenD 的服务，逐项检查 Risks 里没有验证过的行为，确认登录成功、只在 127.0.0.1 的 11111 和 22222 监听；然后退出 Mac 上的 OpenD
9. 启动主程序：日志里看到「启动完成」；在 Discord 里运行 `/plan close symbol:XYZ`，回复没有这只股票的持仓
10. 备份：启用定时器，手动跑一次，做一次恢复演练
11. 重启机器，确认全部自己起来
12. Mac 上经隧道运行一次 OpenD 的冒烟测试

之后是日常检查和查看日志、更新和回滚、停止主程序（本机冒烟测试前）、恢复备份、全部重置、OpenD（重新登录、运行中输入验证码、要回行情权限、升级）、更新 PostgreSQL 小版本、Mac 上开发。

部署机器上看日志用 `sudo journalctl`：管理员不一定在能读系统日志的组里。

部署说明里的命令写成「在哪台机器、以谁的身份」开头的代码块，避免在 Mac 上照着运行。

### D13 测试

`tests/deploy/files.py` 提供部署文件的路径和两个解析函数：

```python
DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
Unit = dict[str, dict[str, list[str]]]


def read_unit(name: str) -> Unit:
    """读 deploy/systemd/<name>：节 → 键 → 值的列表（同一个键可以写多行）。"""
    sections: Unit = {}
    current: dict[str, list[str]] = {}
    for raw in (DEPLOY / "systemd" / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        else:
            key, _, value = line.partition("=")
            current.setdefault(key, []).append(value)
    return sections


def read_env_template(name: str) -> list[tuple[str, str]]:
    """读 deploy/<name>：按顺序列出 (变量名, 值)，跳过注释和空行。"""
    ...
```

- 单元文件不写续行（`\`），一行一个设置，解析就这么简单
- `test_env_templates.py`：主程序模板的变量名等于 `VARIABLES`（顺序也一样）；三个模板的值都为空；单元里每个 `EnvironmentFile` 和 compose 的 `env_file` 都在 `/etc/tradingagentspro/` 下，并且 `deploy/<文件名>.example` 存在；OpenD 单元引用的 `${MOOMOO_LOGIN_ACCOUNT}` 在 OpenD 的模板里
- `test_units.py`：主程序单元的重启设置（`Restart=on-failure`、`RestartSec` 不短于 30 秒、没有改变退出码含义的三个设置、`StartLimitIntervalSec=0`、`TimeoutStopSec=90`）、`ExecStart` 是 `.venv/bin/tradingagentspro run`、运行身份、`Wants`/`After` 有状态库单元而没有 OpenD 单元；状态库单元的 `up --wait` 和 300 秒；OpenD 单元的 `Restart=always`、30 秒、`-login_by_remember=1`、`-no_monitor=1`、`-auto_hold_quote_right=1`，`-api_ip`、`-api_port` 等于 `MoomooConfig()` 的默认值；定时器是每天 06:00 UTC、`Persistent=true`；备份单元以 root 运行、参数是 `/var/backups/tradingagentspro`；主程序、状态库、OpenD 的单元挂在 `multi-user.target`，定时器挂在 `timers.target`，备份服务没有 `[Install]`（只由定时器启动，免得被单独启用成开机就备份）
- `test_compose.py`：只有 `postgres` 一个服务；镜像是 `postgres:18`；每个端口都以 `127.0.0.1:` 开头；数据卷挂在 `/var/lib/postgresql`；`environment` 里没有 `POSTGRES_PASSWORD`；健康检查用 `pg_isready` 和 `--host=127.0.0.1`；`restart: unless-stopped`
- `test_backup.py`：在临时目录里放一个假的 `docker`（sh 脚本：把参数记到文件里，按环境变量决定输出 `DUMP` 还是以 1 退出），放到 `PATH` 最前面，运行 `deploy/backup.sh <临时目录>`：
  - 成功：多一个文件名形如 `20261008T060000Z.dump`、内容是 `DUMP` 的文件；没有 `incomplete`；`docker` 收到的参数里有 `pg_dump` 和 `--format=custom`，没有 `--schema`、`-n`
  - 已有 15 个旧备份（名字是 2000 年的时刻）和一个别的文件：成功后剩 14 个 `.dump`，删掉的是最旧的 2 个，别的文件还在
  - 已有 3 个：成功后 4 个都在
  - `docker` 失败：脚本退出码不是 0；15 个旧备份都在；没有新的 `.dump`
  - 脚本要有可执行权限（单元直接运行它），直接运行就顺带检查了
- compose 文件用 PyYAML 解析：它已经随 dbos 装着，测试直接 import 它，所以加进开发依赖写明；mypy strict 要类型标注，加 `types-pyyaml`
- CI 不运行 `systemd-analyze verify` 和 `docker compose config`：前者要求单元里的路径和用户在 CI 机器上存在，后者要求 `/etc/tradingagentspro/postgres.env` 存在，都要为 CI 造一套假的环境；这两条命令放进部署说明，在部署机器上运行。实现时在本机的临时 Debian 13 容器里用占位文件跑过一次 `systemd-analyze verify`（systemd 257），没有报错
- 进程的测试时区是 UTC+14：备份脚本用 `date -u`，不受影响

## Risks / Trade-offs

- [OpenD 在 systemd 下的行为没有在 Linux 上验证过：控制台模式在没有终端输入时是否正常、`-no_monitor=1` 时是不是一个前台进程、记住的密码在 systemd 启动时能不能用、Ubuntu 的构建在 Debian 上缺不缺库] → 部署说明的 OpenD 一步逐项检查：服务是 active、只有一个 OpenD 进程、CPU 占用接近 0、日志里登录成功、11111 和 22222 在 127.0.0.1 上监听。不符时停下来告诉我，按看到的现象改单元（比如加 `-console=0`），先改 spec 和 design
- [`OpenD.xml` 里 `telnet_port` 的写法没有文字文档] → 部署说明让人在自带文件里找到 `telnet_port` 那一行改值（自带文件里是注释掉的示例）；`ss -ltn` 看到 22222 才算完成
- [记住的登录过期以后 OpenD 登录不上，盘中行情停止] → 主程序进入降级模式，暂停开仓，券商那边的止损单照常挂着；「健康监控接线」以后会推送降级告警；部署说明有重新登录的步骤
- [服务用户能改 root 运行的 compose 文件和备份脚本（它们在服务用户的克隆里），等于能拿到 root] → 这台机器只跑本系统，服务用户本来就持有全部密钥；要避免就得让 root 拥有代码、另找 Python 的安装位置、更新命令都要 root，不值得
- [恢复备份以后止损单的序号退回，挂单可能被拒] → spec「恢复备份」：模拟盘阶段改为全部重置；M7 实盘之前再考虑
- [Alpaca 重置后 key 是否失效、后台的按钮名字，只来自社区论坛] → 部署说明一律在重置后生成新 key，主程序启动时的账户设置就能验证 key 可用
- [主程序、OpenD 和状态库都在一台机器上，机器坏了全停] → 券商那边的止损单一直挂着；Proposal 定的就是一台机器
- [备份失败没有告警] → 日常检查；「健康监控接线」决定是否加进告警
- [本机忘了停部署机器上的主程序就跑冒烟测试，按钮或 `/plan` 被另一个进程收走] → 部署说明和 README 的冒烟测试说明都写明先停

## Migration Plan

- 第一次部署：tasks 的最后一组，由人在部署机器上按 `deploy/README.md` 操作；状态库是新的，启动时由迁移建表
- 本机 Mac 上的 OpenD：部署机器的 OpenD 登录以后退出，不再使用
- 回滚整个部署：`systemctl disable --now` 主程序、OpenD、状态库和备份定时器；数据卷和备份留在机器上
