# Tasks

## 1. 依赖

- [x] 1.1 `uv add --dev pyyaml types-pyyaml`，更新 `uv.lock`（design D13）。验证：`uv run python -c "import yaml"` 成功；`uv run mypy`、`uv run lint-imports` 通过；`uv run pytest` 和加依赖之前一样全部通过；`uv sync --locked --no-dev` 不装 `types-pyyaml`

## 2. 环境变量模板

- [x] 2.1 先写测试：`tests/deploy/__init__.py`（空文件，和其他测试目录一样）、`tests/deploy/files.py`（`DEPLOY`、`read_unit`、`read_env_template`，design D13）、`tests/deploy/test_env_templates.py`：spec「密钥」的「模板和程序读取的变量一致」（主程序模板的变量名和顺序等于 `VARIABLES`）「模板里没有值」（三个模板）；OpenD 的模板只有 `MOOMOO_LOGIN_ACCOUNT`，PostgreSQL 的模板只有 `POSTGRES_PASSWORD`。每个测试的说明写明对应的 spec 场景。验证：确认因为模板文件不存在而失败
- [x] 2.2 写 `deploy/tradingagentspro.env.example`、`deploy/postgres.env.example`、`deploy/opend.env.example`（design D7）：开头说明复制到哪里、权限和怎样填；每个变量前一行注释写格式和从哪里拿，值为空。验证：2.1 的测试通过；在 `VARIABLES` 末尾临时加一个变量，测试失败并写出这个变量名，然后改回；`git check-ignore deploy/*.example` 没有输出（模板没被忽略）

## 3. 状态库

- [x] 3.1 先写测试 `tests/deploy/test_compose.py`（design D13）：spec「监听的端口」的「只在本机监听」中 PostgreSQL 的部分（每个端口以 `127.0.0.1:` 开头）；只有 `postgres` 一个服务、镜像 `postgres:18`、数据卷挂在 `/var/lib/postgresql`、`environment` 里没有 `POSTGRES_PASSWORD`（spec「密钥」）、`env_file` 是 `/etc/tradingagentspro/postgres.env` 并且有对应的模板、健康检查用 `pg_isready` 和 `--host=127.0.0.1`、`restart: unless-stopped`（spec「自动重启」的「PostgreSQL 容器崩溃」）。验证：确认因为 `deploy/compose.yaml` 不存在而失败
- [x] 3.2 写 `deploy/compose.yaml`（design D4）。验证：3.1 的测试通过；在 scratchpad 里做一份副本，只把 `env_file` 换成临时文件、端口换成一个空闲的本机端口、项目名换成 `tradingagentspro-check`、日志驱动换成 json-file（OrbStack 没有 journald），运行 `docker compose up --wait` 能等到健康，用 `psql`（容器里）以 `tradingagentspro` 连上 `tradingagentspro` 库，数据目录是卷里的 `/var/lib/postgresql/18/docker`；顺带按 design D8、D10 的命令做一次导出、删库、建库、恢复，两个 schema 的数据都回来；最后 `docker compose down -v`，删掉临时文件

## 4. systemd 单元

- [x] 4.1 先写测试 `tests/deploy/test_units.py`（design D13）：spec「自动重启」表的每一行（主程序 `Restart=on-failure`、`RestartSec` 不短于 30 秒、没有 `SuccessExitStatus`/`RestartPreventExitStatus`/`RestartForceExitStatus`、`StartLimitIntervalSec=0`；OpenD `Restart=always`、30 秒）和「停止超时」（`TimeoutStopSec=90`）；「启动顺序」（主程序 `Wants`、`After` 有 `tradingagentspro-db.service`，没有 OpenD 的单元；状态库单元 `Requires`、`After` docker、`up --wait`、`TimeoutStartSec=300`）；「监听的端口」的「OpenD 的地址和程序一致」（`-api_ip`、`-api_port` 等于 `MoomooConfig()` 的默认值）；「OpenD 的登录和行情权限」（`-login_by_remember=1`、`-auto_hold_quote_right=1`、`-no_monitor=1`、`${MOOMOO_LOGIN_ACCOUNT}` 在 OpenD 的模板里）；「备份」（定时器 `OnCalendar=*-*-* 06:00:00 UTC`、`Persistent=true`，备份单元不设 `User`、参数是 `/var/backups/tradingagentspro`）；「密钥」（每个 `EnvironmentFile` 在 `/etc/tradingagentspro/` 下并且有 `deploy/<文件名>.example`；单元里没有 `Environment=` 设密钥，只有 `PYTHONUNBUFFERED`）；主程序和 OpenD 以 `tradingagentspro` 运行，`ExecStart` 是 `.venv/bin/tradingagentspro run`；主程序、状态库、OpenD 和定时器都有 `[Install]`，备份服务没有（只由定时器启动）。验证：确认因为单元文件不存在而失败
- [x] 4.2 写 `deploy/systemd/` 下的五个文件（design D4、D5、D6、D8）。验证：4.1 的测试通过；单元里没有续行；`uv run pytest tests/deploy` 全部通过；在本机一个临时的 `debian:13` 容器里装上 systemd，按单元里的路径放好空的可执行文件、环境变量文件和服务用户，`systemd-analyze verify` 没有报错，`systemd-analyze calendar` 认得定时器的时刻；用完删掉容器和镜像

## 5. 备份脚本

- [x] 5.1 先写测试 `tests/deploy/test_backup.py`（design D13）：假的 `docker` 放在临时目录、排在 `PATH` 最前面；覆盖 spec「备份」的「每天备份」（新文件名形如 `20261008T060000Z.dump`、内容来自 `pg_dump`、参数有 `--format=custom` 没有 `--schema`/`-n`、没有留下 `incomplete`）「超过保留份数」（15 个旧的加一个别的文件）「不到保留份数」（3 个）「备份失败」（退出码不是 0、旧备份都在、没有新的 `.dump`），以及「部署的配置项」的「改保留份数」（用改了 `KEEP` 的脚本副本）。验证：确认因为 `deploy/backup.sh` 不存在而失败
- [x] 5.2 写 `deploy/backup.sh`（design D8），加可执行权限。验证：5.1 的测试通过；用 `dash`（CI 和 Debian 的 `/bin/sh`）运行一次也通过；`ls -l deploy/backup.sh` 有 x 权限，`git add` 以后 `git ls-files -s deploy/backup.sh` 的模式是 100755

## 6. 部署说明

- [x] 6.1 写 `deploy/README.md`（design D12）：第一次部署的 12 步和之后的各项操作，每一步写明在哪台机器上、以谁的身份运行，带一条检查命令和预期结果；路径、单元名、变量名和 `deploy/` 里的文件一致；OpenD 一节写明 design「Risks」里未验证的几项要逐项检查、不符时停下来报告。验证：逐条对照 spec 每个要求里由人操作的部分（第一次部署、时钟、OpenD 登录和验证码、要回行情权限、初始资金和全部重置、Discord 授权、日志、备份和恢复、更新和回滚、本机开发），都能在说明里找到对应的步骤；`grep -o 'tradingagentspro[-a-z]*\.\(service\|timer\)' deploy/README.md | sort -u` 的单元名都在 `deploy/systemd/` 里；`uv run ruff format --check` 通过
- [x] 6.2 README：「常用命令」之后加「部署」一行，链接 `deploy/README.md`；moomoo 冒烟测试那一行写明部署以后经 SSH 隧道连部署机器上的 OpenD，本机不再开 OpenD；Alpaca 和 Discord 的冒烟测试写明先停部署机器上的主程序（design D11）。验证：链接能打开；三处说明和 `deploy/README.md` 一致

## 7. 收尾

- [x] 7.1 对照 `specs/deployment/spec.md`，逐条列出每个场景由哪个测试覆盖，或者由第 8 组的哪一项在部署机器上验证；没有遗漏。验证：对应表写在完成报告里
- [x] 7.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过

## 8. 部署机器（由人操作，按 `deploy/README.md`「第一次部署」的 12 步）

- [x] 8.1 【人】第 1 步，部署机器的前提：Debian 12 或 13、x86_64；时间同步；Docker Engine 和 compose 插件；`git`、`telnet`；uv 装到 `/usr/local/bin`；journald 持久保存。验证：`uname -m` 是 `x86_64`；`timedatectl` 显示 `System clock synchronized: yes`；`sudo docker compose version`、`uv --version`、`git --version` 都有输出；`/var/log/journal` 里有目录
- [x] 8.2 【人】第 2 步，服务用户、Deploy key、克隆、安装依赖。验证：`ssh -T git@github.com`（服务用户）回复已认证；GitHub 的 Deploy keys 页面上这把 key 是 Read-only；`~/app/.venv/bin/tradingagentspro --version` 打印版本号
- [ ] 8.3 【人】第 3 步，Alpaca 模拟盘重置为 2,000 美元并生成新 key；核对 Discord 机器人的 scope 和频道权限。验证：Alpaca 后台模拟盘的权益是 $2,000、没有持仓和挂单；机器人在频道里有「查看频道」「发送消息」「嵌入链接」
- [x] 8.4 【人】第 4 步，三个环境变量文件：生成数据库密码，填好七个变量、PostgreSQL 密码和 moomoo ID。验证：`sudo ls -la /etc/tradingagentspro` 显示目录 `drwx------ root`、三个文件 `-rw------- root`；`sudo -u tradingagentspro cat /etc/tradingagentspro/tradingagentspro.env` 提示没有权限；`sudo grep -c '=$' …` 三个文件都是 0
- [x] 8.5 【人】第 5 步，OpenD：下载和 `uv.lock` 里 moomoo-api 同版本的 Linux 命令行 OpenD，设 `telnet_port`，第一次交互登录（记住密码，必要时输入手机验证码）。验证：OpenD 提示登录成功；`grep -n telnet ~/opend/OpenD.xml` 里 `telnet_port` 是 22222、没有被注释
- [x] 8.6 【人】第 6、7 步，安装单元、启动状态库。验证：`sudo systemd-analyze verify /etc/systemd/system/tradingagentspro*` 没有输出；`sudo docker compose ps` 显示 `healthy`；`ss -ltn` 里 5432 只在 127.0.0.1 上
- [x] 8.7 【人】第 8 步，启动 OpenD 的服务，然后退出 Mac 上的 OpenD。验证：`systemctl status tradingagentspro-opend` 是 active，一分钟后还是同一个进程；只有一个 OpenD 进程、CPU 占用接近 0；`~/.com.moomoo.OpenD/Log` 里有以 moomoo ID 命名的 `GTWLog_<ID>_…` 文件（OpenD 的输出不进 journald）；`ss -ltn` 里 11111 和 22222 只在 127.0.0.1 上；经 `telnet 127.0.0.1 22222` 发 `help` 有回复。任何一项不符，停下来把现象告诉 Claude
- [ ] 8.8 【人】第 9 步，启用并启动主程序。验证：`sudo journalctl -u tradingagentspro` 依次有数据库已升级、Alpaca 账户设置完成、「启动对账：没有不一致」、Discord 已连接、DBOS 已启动、「启动完成」；在 Discord 里运行 `/plan close symbol:XYZ`，回复没有 XYZ 的持仓；`sudo ss -ltnp | grep python` 没有输出
- [x] 8.9 【人】第 10 步，启用备份定时器，手动备份一次，做一次恢复演练。验证：`systemctl list-timers` 里有下一次 06:00 UTC 的备份；`/var/backups/tradingagentspro` 里有一个 `.dump`；恢复到临时数据库以后 `\dn` 列出 `public` 和 `dbos`，演练完删掉临时数据库
- [ ] 8.10 【人】第 11 步，重启部署机器。验证：开机后不做任何操作，状态库、OpenD、主程序都在运行；这次开机的日志里有「启动完成」；`sudo journalctl -u tradingagentspro -b -1` 能看到重启前的日志
- [x] 8.11 【人】第 12 步，在 Mac 上开 SSH 隧道。验证：Mac 上没有 OpenD 进程；`uv run pytest -m moomoo_opend -k test_quotes_at_any_time` 经隧道通过（不是跳过）
