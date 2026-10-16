# 部署

把交易侧部署到一台常开的 x86_64 Debian 机器（下面叫部署机器）上：PostgreSQL 18 在 Docker 里，OpenD 和主程序由 systemd 管理，每天备份状态库。要求见 openspec 的 deployment spec，做法见 add-deployment 变更的 design。

## 总览

| 服务 | systemd 单元 | 运行身份 | 监听 |
| --- | --- | --- | --- |
| 状态库（Docker 里的 PostgreSQL 18） | `tradingagentspro-db.service` | root（Docker） | 127.0.0.1:5432 |
| OpenD（命令行版） | `tradingagentspro-opend.service` | `tradingagentspro` | 127.0.0.1:11111（API）、127.0.0.1:22222（远程命令） |
| 主程序（`tradingagentspro run`） | `tradingagentspro.service` | `tradingagentspro` | 不监听 |
| 备份（每天 06:00 UTC） | `tradingagentspro-backup.timer` → `tradingagentspro-backup.service` | root | — |

| 路径 | 是什么 |
| --- | --- |
| `/opt/tradingagentspro` | 服务用户 `tradingagentspro` 的主目录 |
| `/opt/tradingagentspro/app` | 仓库的克隆，`.venv` 在里面 |
| `/opt/tradingagentspro/opend` | 命令行版 OpenD |
| `/opt/tradingagentspro/.com.moomoo.OpenD/Log` | OpenD 和 moomoo SDK 自己的日志文件 |
| `/etc/tradingagentspro/` | 三个环境变量文件，只有 root 能读（模板是本目录的 `*.env.example`） |
| `/var/backups/tradingagentspro/` | 备份，只有 root 能读 |
| `/etc/systemd/system/tradingagentspro*` | 从本目录 `systemd/` 复制过去的单元 |

约定：

- 每个代码块的第一行注释写明在哪台机器、以谁的身份运行：
  - 「部署机器 · 管理员」：你平时登录部署机器、能用 sudo 的用户
  - 「部署机器 · 服务用户」：先以管理员运行 `sudo -iu tradingagentspro` 切过去，做完 `exit` 回来
  - 「Mac」：开发用的 Mac
- 每一步后面的「检查」和预期一致才继续。不一致时停下来，把命令和输出发给 Claude
- 密钥只填进部署机器上 `/etc/tradingagentspro/` 里的文件，不写进仓库，也不发到聊天里

## 第一次部署

### 1. 机器的前提

```bash
# 部署机器 · 管理员
uname -m
cat /etc/debian_version
timedatectl
```

检查：`x86_64`；Debian 12 或 13；`System clock synchronized: yes`、`NTP service: active`。时间没有同步时：

```bash
# 部署机器 · 管理员
sudo apt install systemd-timesyncd
sudo timedatectl set-ntp true
```

按 Docker 官方文档 <https://docs.docker.com/engine/install/debian/> 的「Install using the apt repository」装好 Docker Engine 和 compose 插件（`docker-ce`、`docker-compose-plugin` 等）。再装其余的工具，并让 journald 跨重启保留日志（Debian 13 默认就保留，Debian 12 要建这个目录）：

```bash
# 部署机器 · 管理员
sudo apt install git telnet curl openssl
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```

检查：

```bash
# 部署机器 · 管理员
sudo docker compose version
uv --version
git --version
ls /var/log/journal
```

预期：三个版本号都打印出来；`/var/log/journal` 里有一个以 machine-id 命名的目录。

### 2. 服务用户和代码

```bash
# 部署机器 · 管理员
sudo adduser --system --group --home /opt/tradingagentspro --shell /bin/bash tradingagentspro
sudo -iu tradingagentspro
```

```bash
# 部署机器 · 服务用户
mkdir -p -m 700 ~/.ssh
ssh-keygen -t ed25519 -N "" -C "tradingagentspro deploy key" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

在 GitHub 仓库的 Settings → Deploy keys → Add deploy key：标题写 `debian host`，粘贴上面打印的公钥，**不要**勾选 Allow write access。然后：

```bash
# 部署机器 · 服务用户
ssh -T git@github.com
```

第一次连接会显示 GitHub 的主机指纹，和 <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints> 上的对上了再输入 `yes`。检查：回复 `Hi wljf817/TradingAgentsPro! You've successfully authenticated`。

```bash
# 部署机器 · 服务用户
git clone git@github.com:wljf817/TradingAgentsPro.git ~/app
cd ~/app && uv sync --locked --no-dev
~/app/.venv/bin/tradingagentspro --version
exit
```

检查：打印 `tradingagentspro <版本号>`；GitHub 的 Deploy keys 页面上这把 key 显示 Read-only。

### 3. Alpaca 模拟盘和 Discord 机器人

**Alpaca**：登录 Alpaca 后台，重置模拟盘（Paper）账户，初始资金填 **2000**（打算用于实盘的资金）。重置以后旧的 key 不再可用，在 API Keys 里生成一对新的，secret 只显示一次，先记在只有你能看到的地方，下一步填进环境变量文件。

> 重置模拟盘账户和清空状态库必须一起做（见「全部重置」）。第一次部署时状态库是空的，所以这里重置一次。

检查：模拟盘账户的权益是 $2,000，没有持仓，没有挂单。

**Discord**：机器人已经在服务器里（Discord 冒烟测试用过）时只需核对：

- 机器人加入服务器时的 scope 有 `bot` 和 `applications.commands`（在频道里输入 `/plan` 能看到这条命令就是有）
- 告警和确认的频道里，机器人（或它的角色）有「查看频道」「发送消息」「嵌入链接」；「读取消息历史」只有在本机跑 Discord 冒烟测试时才要
- 你自己在这个频道里有「使用应用命令」（默认就有）
- 开发者后台 Bot 页的 Privileged Gateway Intents 一项都不用开

还没有机器人时：在 Discord 开发者后台新建应用 → Bot 页 Reset Token 拿到 token → OAuth2 的 URL Generator 勾选 `bot` 和 `applications.commands`，Bot Permissions 勾选 View Channels、Send Messages、Embed Links → 用生成的链接把机器人邀请进服务器。

服务器、频道和你自己的 ID：Discord 的用户设置 → 高级 → 打开开发者模式，然后右键服务器、频道、自己的头像，「复制 ID」。

### 4. 环境变量文件

```bash
# 部署机器 · 管理员
sudo install -d -m 700 /etc/tradingagentspro
for name in tradingagentspro postgres opend; do
  sudo install -m 600 /opt/tradingagentspro/app/deploy/$name.env.example /etc/tradingagentspro/$name.env
done
openssl rand -hex 24
```

最后一行打印的是数据库密码，下面两个文件都要用它。逐个编辑（`sudo nano <文件>`），按文件里的注释在等号后面填值，不加引号：

- `/etc/tradingagentspro/postgres.env`：`POSTGRES_PASSWORD=<数据库密码>`
- `/etc/tradingagentspro/tradingagentspro.env`：
  - `TRADINGAGENTSPRO_DATABASE_URL=postgresql://tradingagentspro:<数据库密码>@127.0.0.1:5432/tradingagentspro`
  - 第 3 步新生成的 Alpaca key 和 secret
  - Discord 的 token 和三个 ID
- `/etc/tradingagentspro/opend.env`：`MOOMOO_LOGIN_ACCOUNT=<moomoo ID>`（数字的 moomoo ID，不用手机号）

检查：

```bash
# 部署机器 · 管理员
sudo ls -la /etc/tradingagentspro
sudo -u tradingagentspro cat /etc/tradingagentspro/tradingagentspro.env
sudo grep -c '=$' /etc/tradingagentspro/tradingagentspro.env /etc/tradingagentspro/postgres.env /etc/tradingagentspro/opend.env
```

预期：目录是 `drwx------ root root`，三个文件都是 `-rw------- root root`；服务用户读文件时提示 `Permission denied`；三个文件后面的数都是 0（没有空着的变量）。

### 5. 安装 OpenD 并第一次登录

OpenD 的版本要和代码用的 moomoo-api 一样：

```bash
# 部署机器 · 服务用户
grep -A1 'name = "moomoo-api"' ~/app/uv.lock
```

在 moomoo 官网的 OpenAPI 下载页 <https://www.moomoo.com/download/OpenAPI> 找到这个版本的 OpenD，选 Linux（Ubuntu）版，复制下载链接，在部署机器上下载和解压：

```bash
# 部署机器 · 服务用户
cd ~
curl -L -o opend-download '<下载链接>'
mkdir opend-unpacked && tar -xf opend-download -C opend-unpacked
find opend-unpacked -name OpenD -type f
```

`find` 找到的那个 `OpenD` 所在的目录就是命令行版（包里另有一个带 GUI 字样的图形界面版，不用）。把这个目录复制成 `~/opend`：

```bash
# 部署机器 · 服务用户
cp -r '<find 找到的 OpenD 所在的目录>' ~/opend
ls ~/opend
rm -rf ~/opend-download ~/opend-unpacked
```

检查：`~/opend` 里有 `OpenD`、`OpenD.xml` 和 `Appdata.dat`（大小写以包里的为准）。

打开远程命令端口：`nano ~/opend/OpenD.xml`，找到 `telnet_port` 那一行，改成 `<telnet_port>22222</telnet_port>`（原来被 `<!--` `-->` 注释掉的话，去掉注释）；`telnet_ip` 保持注释或者 `127.0.0.1`。其余设置不用改：API 的地址和端口由 systemd 单元在命令行上指定。

```bash
# 部署机器 · 服务用户
grep -n telnet ~/opend/OpenD.xml
```

检查：`telnet_port` 是 22222，没有被注释；`telnet_ip` 没有设成 127.0.0.1 以外的地址。

第一次登录，在终端里交互进行：

```bash
# 部署机器 · 服务用户
cd ~/opend && ./OpenD -api_ip=127.0.0.1 -api_port=11111
```

- 按提示输入账号（moomoo ID）和密码；问是否记住密码时选「是」
- 要求手机验证码时：收到短信后在 OpenD 的控制台输入 `input_phone_verify_code -code=<验证码>`（注意 `-code` 前有空格）；没收到短信先输入 `req_phone_verify_code`（60 秒只能请求一次）
- 看到登录成功以后，输入 `exit` 退出 OpenD，再输入 `exit` 回到管理员

检查：OpenD 提示登录成功；`ls /opt/tradingagentspro/.com.moomoo.OpenD` 能看到这个目录。OpenD 起不来（比如缺少某个库）时，把报错发给 Claude。

### 6. 安装 systemd 单元

```bash
# 部署机器 · 管理员
sudo install -m 644 /opt/tradingagentspro/app/deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/tradingagentspro*
```

检查：`systemd-analyze verify` 没有输出。

### 7. 启动状态库

```bash
# 部署机器 · 管理员
cd /opt/tradingagentspro/app/deploy
sudo docker compose config --quiet
sudo docker compose pull
sudo systemctl enable --now tradingagentspro-db
sudo docker compose ps
ss -ltn | grep 5432
```

检查：`config --quiet` 没有输出；`docker compose ps` 里 `postgres` 是 `healthy`，端口是 `127.0.0.1:5432->5432/tcp`；`ss` 只显示 `127.0.0.1:5432`。

### 8. 启动 OpenD

```bash
# 部署机器 · 管理员
sudo systemctl enable --now tradingagentspro-opend
```

逐项检查，任何一项不符就停下来，把输出发给 Claude（这几项没能事先在 Linux 上验证）：

```bash
# 部署机器 · 管理员
systemctl status tradingagentspro-opend --no-pager
ps -o pid,ppid,%cpu,cmd -u tradingagentspro | grep '[O]penD'
sudo journalctl -u tradingagentspro-opend -n 50 --no-pager
ss -ltn | grep -E ':(11111|22222) '
```

- `status` 是 `active (running)`；过一分钟再看一次，还是同一个进程，没有反复重启
- 只有一个 OpenD 进程，CPU 占用接近 0
- 登录成功的标志：`sudo ls -t /opt/tradingagentspro/.com.moomoo.OpenD/Log | head` 里有这次启动以后新出现的、以 moomoo ID 命名的 `GTWLog_<moomoo ID>_…` 文件（只有登录以后才有）。OpenD 的控制台输出不进 journald，journal 里只有 systemd 的启动记录
- 只有 `127.0.0.1:11111` 和 `127.0.0.1:22222`

再试一下远程命令：

```bash
# 部署机器 · 管理员
telnet 127.0.0.1 22222
```

连上以后输入 `help` 回车，应该有回复；按 `Ctrl-]`，再输入 `quit` 退出。

部署机器上的 OpenD 登录以后，**退出 Mac 上的 OpenD，以后不再开**，否则两边会抢行情权限（见「Mac 上开发」）。

### 9. 启动主程序

```bash
# 部署机器 · 管理员
sudo systemctl enable --now tradingagentspro
sudo journalctl -u tradingagentspro -f
```

检查：日志依次出现「启动 tradingagentspro …（模拟盘）」「数据库已升级到最新版本」「交易日历」「Alpaca 账户设置完成」「启动对账：没有不一致」「健康：正常」（常规交易时段内 OpenD 有问题时是降级的 WARNING）「Discord 已连接」「DBOS 已启动」「启动完成」。按 `Ctrl-C` 退出查看（程序不受影响）。

然后在 Discord 的频道里运行 `/plan close symbol:XYZ`，回复说没有 XYZ 的持仓。最后确认主程序没有监听端口：

```bash
# 部署机器 · 管理员
sudo ss -ltnp | grep python
```

预期：没有输出。

### 10. 备份和恢复演练

```bash
# 部署机器 · 管理员
sudo systemctl enable --now tradingagentspro-backup.timer
systemctl list-timers tradingagentspro-backup.timer --no-pager
sudo systemctl start tradingagentspro-backup
sudo ls -l /var/backups/tradingagentspro
```

检查：定时器的下一次时刻是 06:00 UTC（按本机时区显示）；备份目录里有一个 `<UTC 时刻>.dump`。

恢复演练：把最新的备份恢复到一个临时数据库，看两个 schema 都在，再删掉：

```bash
# 部署机器 · 管理员
cd /opt/tradingagentspro/app/deploy
sudo docker compose exec -T postgres createdb --username=tradingagentspro restore_check
sudo sh -c 'docker compose exec -T postgres pg_restore --username=tradingagentspro --dbname=restore_check < "$(ls /var/backups/tradingagentspro/*.dump | tail -n 1)"'
sudo docker compose exec -T postgres psql --username=tradingagentspro --dbname=restore_check -c '\dn'
sudo docker compose exec -T postgres dropdb --username=tradingagentspro restore_check
```

检查：`\dn` 列出 `dbos` 和 `public`。

### 11. 重启检查

```bash
# 部署机器 · 管理员
sudo reboot
```

重新登录以后，什么都不做，直接检查：

```bash
# 部署机器 · 管理员
systemctl status tradingagentspro-db tradingagentspro-opend tradingagentspro --no-pager
sudo journalctl -u tradingagentspro -b --no-pager | grep 启动完成
sudo journalctl -u tradingagentspro -b -1 --no-pager | tail -n 5
systemctl list-timers tradingagentspro-backup.timer --no-pager
```

预期：三个服务都在运行；这次开机的日志里有「启动完成」；上一次开机的日志还在；定时器还在。

### 12. Mac 上经隧道连 OpenD

```bash
# Mac
ssh -N -L 11111:127.0.0.1:11111 <登录部署机器用的 用户@地址>
```

隧道开着的时候，在另一个终端：

```bash
# Mac
uv run pytest -m moomoo_opend -k test_quotes_at_any_time
```

检查：Mac 上没有 OpenD 在运行；测试 `1 passed`（不是 skipped）。

## 日常检查

```bash
# 部署机器 · 管理员
systemctl --failed --no-pager
sudo ls -lt /var/backups/tradingagentspro | head -n 3
sudo journalctl -u tradingagentspro --since today --no-pager | grep -E ' (WARNING|ERROR) '
```

- `--failed` 里没有 `tradingagentspro` 开头的单元（备份失败会出现在这里）
- 最新的备份是最近一次 06:00 UTC 以后的
- 有 WARNING 或 ERROR 时看前后的日志

查看日志：

```bash
# 部署机器 · 管理员
sudo journalctl -u tradingagentspro -f
sudo journalctl -u tradingagentspro-opend -n 100 --no-pager
sudo journalctl -u tradingagentspro-backup -n 20 --no-pager
cd /opt/tradingagentspro/app/deploy && sudo docker compose logs --tail 50 postgres
```

## 查看程序日志

程序的动作（风控、订单和止损单、人工确认、对账、降级、紧急退出、告警，以及启动和停止）记在状态库的 `programlog_entries` 表里，一个动作一行，只追加；journald 里只有启动、停止的过程和技术问题。用 psql 查：

```bash
# 部署机器 · 管理员
cd /opt/tradingagentspro/app/deploy && sudo docker compose exec postgres psql --username=tradingagentspro --dbname=tradingagentspro
```

```sql
-- 最近 50 条
select id, recorded_at, actor, action, subject, detail
from programlog_entries order by id desc limit 50;

-- 一个方案的全部条目
select id, recorded_at, actor, action, subject, detail
from programlog_entries
where subject = '<方案 ID>' or '<方案 ID>' = any(inputs) or '<方案 ID>' = any(outputs)
order by id;

-- 一只股票一天里的条目（止损单的对象以股票代码开头）
select id, recorded_at, actor, action, subject, detail
from programlog_entries
where recorded_at >= '2026-10-14T13:30Z' and recorded_at < '2026-10-15T13:30Z'
  and (subject = 'AAA' or subject like 'AAA-STOP-%' or detail like '%AAA%')
order by id;
```

时间是 UTC。行为者和动作是英文代码，含义见 openspec 的 program-log spec 和 add-program-log 变更的 design D1。

## 更新

1. 先备份：

   ```bash
   # 部署机器 · 管理员
   sudo systemctl start tradingagentspro-backup
   sudo ls -lt /var/backups/tradingagentspro | head -n 2
   ```

2. 拉代码，看这次改了什么：

   ```bash
   # 部署机器 · 服务用户
   cd ~/app && git pull --ff-only
   git log -1 --oneline
   git diff --stat 'HEAD@{1}' -- deploy uv.lock
   grep -A1 'name = "moomoo-api"' uv.lock
   ```

3. moomoo-api 的版本变了：先按「升级 OpenD」把 OpenD 升到同一个版本
4. 同步依赖：

   ```bash
   # 部署机器 · 服务用户
   cd ~/app && uv sync --locked --no-dev
   exit
   ```

5. `deploy/` 没有变：

   ```bash
   # 部署机器 · 管理员
   sudo systemctl restart tradingagentspro
   ```

   `deploy/` 变了：有 `*.env.example` 的改动，先按新模板补上 `/etc/tradingagentspro/` 里的变量；然后

   ```bash
   # 部署机器 · 管理员
   sudo systemctl stop tradingagentspro
   sudo install -m 644 /opt/tradingagentspro/app/deploy/systemd/* /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl restart tradingagentspro-db
   sudo systemctl start tradingagentspro
   ```

   改动里有 `tradingagentspro-opend.service` 时再 `sudo systemctl restart tradingagentspro-opend`；有定时器时再 `sudo systemctl restart tradingagentspro-backup.timer`

6. 检查：`sudo journalctl -u tradingagentspro -n 30 --no-pager` 里有这次启动的「启动完成」；`git log -1 --oneline` 和 GitHub 上 main 的最新提交一样。数据库的迁移在启动时自动运行

**回滚**（更新以后主程序一直启动失败）：

```bash
# 部署机器 · 服务用户
cd ~/app && git log --oneline -5
git checkout '<更新前的提交>'
uv sync --locked --no-dev
exit
```

这次更新带有新的迁移（`src/tradingagentspro/bootstrap/migrations/versions/` 里多了文件）时，先按「恢复备份」用第 1 步的备份恢复状态库；然后 `sudo systemctl restart tradingagentspro`。问题修好以后，以服务用户 `cd ~/app && git checkout main`，再按上面的步骤更新。

## 停止主程序（本机冒烟测试之前）

同一个 Discord token、同一个 Alpaca 账户同时只能有一个主程序在用。在 Mac 上跑连 Discord 或 Alpaca 的冒烟测试（包括 `uv run tradingagentspro run` 的冒烟运行）之前：

```bash
# 部署机器 · 管理员
sudo systemctl stop tradingagentspro
systemctl is-active tradingagentspro
```

预期 `inactive`。部署机器上的 OpenD 不用停，Mac 经隧道连它。测完：

```bash
# 部署机器 · 管理员
sudo systemctl start tradingagentspro
```

## 恢复备份

只在状态库丢失或损坏时用。恢复的是备份时刻的状态，之后在券商那边发生的事由启动对账报出来。

> 备份之后挂过新止损单的股票，恢复以后再挂止损单会用到和券商那边旧订单相同的客户端订单 ID（比如 `AAPL-STOP-3`），挂单被拒。模拟盘阶段遇到启动对账有不一致、或者之后止损单被拒，改做「全部重置」。

```bash
# 部署机器 · 管理员
sudo systemctl stop tradingagentspro
cd /opt/tradingagentspro/app/deploy
sudo docker compose exec -T postgres dropdb --username=tradingagentspro --force tradingagentspro
sudo docker compose exec -T postgres createdb --username=tradingagentspro tradingagentspro
sudo ls /var/backups/tradingagentspro
sudo sh -c 'docker compose exec -T postgres pg_restore --username=tradingagentspro --dbname=tradingagentspro < /var/backups/tradingagentspro/<要恢复的文件>.dump'
sudo systemctl start tradingagentspro
sudo journalctl -u tradingagentspro -n 30 --no-pager
```

检查：「启动对账：没有不一致」和「启动完成」。

## 全部重置

状态库和 Alpaca 模拟盘账户**必须一起**重置：

- 只清空状态库：券商那边留着以前的止损单（比如 `AAPL-STOP-1`），新的止损单从序号 1 重新编号，客户端订单 ID 重复，挂单被拒
- 只重置模拟盘：状态库里还记着持仓和止损单，启动对账报出不一致，暂停开仓

```bash
# 部署机器 · 管理员
sudo systemctl stop tradingagentspro
cd /opt/tradingagentspro/app/deploy
sudo docker compose exec -T postgres dropdb --username=tradingagentspro --force tradingagentspro
sudo docker compose exec -T postgres createdb --username=tradingagentspro tradingagentspro
```

删库会把 `public` 和 DBOS 的 `dbos` 两个 schema 一起删掉。然后在 Alpaca 后台重置模拟盘账户，初始资金填 2000，生成新的 key，用 `sudo nano /etc/tradingagentspro/tradingagentspro.env` 换掉 `APCA_API_KEY_ID` 和 `APCA_API_SECRET_KEY`。最后：

```bash
# 部署机器 · 管理员
sudo systemctl start tradingagentspro
sudo journalctl -u tradingagentspro -n 30 --no-pager
```

检查：「数据库已升级到最新版本」「启动对账：没有不一致」「启动完成」；Alpaca 后台模拟盘的权益是 $2,000。

## OpenD

### 重新登录

记住的登录会过期；改过 moomoo 密码以后也要重新登录。表现：`sudo journalctl -u tradingagentspro-opend` 里登录失败或者 OpenD 反复重启；主程序在常规交易时段内取不到行情、进入降级模式。

```bash
# 部署机器 · 管理员
sudo systemctl stop tradingagentspro-opend
sudo -iu tradingagentspro
```

```bash
# 部署机器 · 服务用户
cd ~/opend && ./OpenD -api_ip=127.0.0.1 -api_port=11111
```

和第一次登录一样：输入账号和密码，选择记住密码，要验证码时输入；登录成功后输入 `exit`，再 `exit` 回到管理员，然后：

```bash
# 部署机器 · 管理员
sudo systemctl start tradingagentspro-opend
sudo journalctl -u tradingagentspro-opend -n 30 --no-pager
```

检查：登录成功的标志：`sudo ls -t /opt/tradingagentspro/.com.moomoo.OpenD/Log | head` 里有这次启动以后新出现的、以 moomoo ID 命名的 `GTWLog_<moomoo ID>_…` 文件（只有登录以后才有）。OpenD 的控制台输出不进 journald，journal 里只有 systemd 的启动记录。

### 运行中输入手机验证码

OpenD 在 systemd 下运行时要求验证码（日志里能看到），不用停服务，经远程命令端口输入：

```bash
# 部署机器 · 管理员
telnet 127.0.0.1 22222
```

连上以后先输入 `req_phone_verify_code`（60 秒只能请求一次），收到短信后输入 `input_phone_verify_code -code=<验证码>`；看到回复以后按 `Ctrl-]`，输入 `quit` 退出。检查：OpenD 的日志里登录成功。

### 要回行情权限

一个账号同时只有一个设备持有最高行情权限。OpenD 被手机 App 或别的设备抢走以后会自动要回；10 秒内被抢走两次就不再自动要回。表现：常规交易时段内主程序取不到实时行情、进入降级模式。

先确认 Mac 上没有开着 OpenD，再：

```bash
# 部署机器 · 管理员
telnet 127.0.0.1 22222
```

输入 `request_highest_quote_right`，看到成功的回复以后按 `Ctrl-]`，输入 `quit` 退出。

### 升级 OpenD

`uv.lock` 里 moomoo-api 升级时，OpenD 一起升到同一个版本：

```bash
# 部署机器 · 管理员
sudo systemctl stop tradingagentspro-opend
sudo -iu tradingagentspro
```

```bash
# 部署机器 · 服务用户
mv ~/opend ~/opend.old
```

按第 5 步下载新版本、复制成 `~/opend`、在新的 `OpenD.xml` 里设 `telnet_port`（不用重新登录）。旧目录里有 `Device.dat` 时复制过来，免得重新验证设备：

```bash
# 部署机器 · 服务用户
ls ~/opend.old/Device.dat && cp ~/opend.old/Device.dat ~/opend/
exit
```

```bash
# 部署机器 · 管理员
sudo systemctl start tradingagentspro-opend
sudo journalctl -u tradingagentspro-opend -n 30 --no-pager
```

检查：登录成功的标志：`sudo ls -t /opt/tradingagentspro/.com.moomoo.OpenD/Log | head` 里有这次启动以后新出现的、以 moomoo ID 命名的 `GTWLog_<moomoo ID>_…` 文件（只有登录以后才有）。OpenD 的控制台输出不进 journald，journal 里只有 systemd 的启动记录（要求验证码时按「运行中输入手机验证码」）。确认没问题以后 `sudo rm -rf /opt/tradingagentspro/opend.old`。

## 更新 PostgreSQL 小版本（可选）

`postgres:18` 只在拉取时更新到 18 的最新小版本：

```bash
# 部署机器 · 管理员
sudo systemctl start tradingagentspro-backup
sudo systemctl stop tradingagentspro
cd /opt/tradingagentspro/app/deploy && sudo docker compose pull
sudo systemctl restart tradingagentspro-db
sudo systemctl start tradingagentspro
```

检查：`sudo docker compose ps` 是 `healthy`；主程序的日志里有「启动完成」。

## Mac 上开发

- 部署机器上的 OpenD 运行以后，Mac 上不再开 OpenD：同一个账号的两个 OpenD 会互相抢最高行情权限，部署机器上的主程序会进入降级模式
- Mac 上要连 OpenD（比如 `uv run pytest -m moomoo_opend`）时，先开隧道 `ssh -N -L 11111:127.0.0.1:11111 <用户@部署机器>`，本机的 127.0.0.1:11111 就转到部署机器的 OpenD，代码和命令都不用改
- Mac 上跑连 Discord 或 Alpaca 的冒烟测试之前，先按「停止主程序」停掉部署机器上的主程序，测完再启动
