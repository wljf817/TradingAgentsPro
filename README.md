# TradingAgentsPro

以代码调度器为核心的多 agent 美股交易系统：只有出现值得关注的事件或检查到期时才开案件，由 PM 带领子 agent 研究并给出结论，交易方案经 Critic 审核、代码风控和人工确认后执行。

- 设计文档：[docs/](docs/)，先读 [Proposal 简版](docs/AI%20Agent%20美股交易系统需求%20Proposal%20简版.md)
- 实现路线和里程碑：[docs/实现路线.md](docs/实现路线.md)
- 规格和变更：[openspec/](openspec/)

## 目录分层

```
src/tradingagentspro/
  bootstrap/      程序入口和装配：选 live / paper / replay，把实现注入各模块
  workflows/      流程：DBOS 工作流和盘中巡检，把多个模块的用例串起来（和 integrations 互相独立）
  integrations/   外部系统的适配器：实现各模块定义的接口
  modules/        业务模块：每个模块对应架构图里的一个节点
  contracts/      跨模块传递的 Pydantic 模型
  shared/         最底层的基础类型和协议
```

上层可以依赖下层，反过来不行。包根和这 6 个层级包的 `__init__.py` 只放说明，不写代码。

每个模块内部的结构是固定的：

```
modules/<模块>/
  __init__.py     公开 API：模块之外的代码只能 import 这里
  service.py      用例：只依赖 ports 里的接口、domain 和 config
  repo.py         本模块的表，以及存储接口的数据库实现
  ports.py        本模块需要的全部外部接口（Protocol）
  domain/         纯逻辑：不做 I/O，不读时间，不依赖本机时区
  config.py       阈值和默认值
```

## 数据库

状态库的表都是只追加的版本表，写法在 `modules/statestore/`，表和它的数据库实现放在拥有这张表的模块的 `repo.py` 里。表由 `bootstrap/migrations/` 里的 Alembic 迁移建立和修改，`bootstrap.database.migrate` 把数据库升级到最新版本。

加一个迁移：

1. 在 `bootstrap/migrations/versions/` 里新建一个文件，文件名以字母开头，比如 `v0002_create_trade_records.py`
2. `revision` 是上一个的编号加 1（比如 `"0002"`），`down_revision` 写上一个的编号
3. `upgrade` 里写明每一列，不 import 业务代码；`downgrade` 直接抛错，迁移只升级

## 常用命令

```bash
uv sync                      # 安装依赖
uv run ruff check            # 代码检查
uv run ruff format --check   # 格式检查
uv run mypy                  # 类型检查（strict）
uv run lint-imports          # 依赖方向检查
uv run pytest                # 测试
uv run pytest -m alpaca_paper  # 连 Alpaca 模拟盘的冒烟测试，需要 APCA_API_KEY_ID、APCA_API_SECRET_KEY
uv run pytest -m moomoo_opend  # 连本机 127.0.0.1:11111 上 OpenD 的行情冒烟测试，OpenD 要开着并已登录
uv run pytest -m discord_bot   # 连真实 Discord 的冒烟测试，需要 DISCORD_BOT_TOKEN、DISCORD_GUILD_ID、DISCORD_CHANNEL_ID、DISCORD_USER_ID；运行中要在 3 分钟内按消息上的按钮，并在 5 分钟内按告警的提示运行四条 /plan
uv run pytest -m discord_bot tests/integrations/discord/test_plan_smoke.py  # 只跑 /plan 的冒烟测试
uv run pytest -m news_sources  # 连真实 Alpaca 新闻和 SEC EDGAR 的冒烟测试：Alpaca 的需要 APCA_API_KEY_ID、APCA_API_SECRET_KEY；SEC 的需要 SEC_USER_AGENT，写名字和联系邮箱，比如 "TradingAgentsPro 你的邮箱"
uv run pytest -m finnhub_api   # 连真实 Finnhub 的冒烟测试，需要 FINNHUB_API_KEY（Finnhub 免费档的 key）；一次约 33 个请求，两次之间隔 1 分钟以上
```

部署机器运行以后，冒烟测试要注意两点（见 [deploy/README.md](deploy/README.md)「Mac 上开发」）：

- 本机不再开 OpenD，先开 SSH 隧道 `ssh -N -L 11111:127.0.0.1:11111 <用户@部署机器>`，`moomoo_opend` 的测试经隧道连部署机器上的 OpenD
- 连 Alpaca 或 Discord 的冒烟测试（包括下面的 `tradingagentspro run`）之前，先停掉部署机器上的主程序（`sudo systemctl stop tradingagentspro`），测完再启动

启动交易侧程序（模拟盘），Ctrl-C 停止：

```bash
uv run tradingagentspro run
```

要先在终端里设好 7 个环境变量（只设在终端或部署机器的环境里，不写进仓库）：`TRADINGAGENTSPRO_DATABASE_URL`（`postgresql://用户:密码@主机:端口/数据库`）、`APCA_API_KEY_ID`、`APCA_API_SECRET_KEY`、`DISCORD_BOT_TOKEN`、`DISCORD_GUILD_ID`、`DISCORD_CHANNEL_ID`、`DISCORD_USER_ID`，并开着 PostgreSQL。启动时自动升级数据库；OpenD 没开时照常启动，盘中进入降级模式。日志写到 stderr。启动失败退出码 1，正常停止退出码 0。

部署到常开的 Debian 机器（PostgreSQL、OpenD、主程序、每天的备份）的文件和步骤在 [deploy/](deploy/)，从 [deploy/README.md](deploy/README.md) 开始。

连数据库的测试会用 testcontainers 启动一个临时的 PostgreSQL 18 容器，用完即删，所以跑完整的测试要开着 Docker（比如 OrbStack 或 Docker Desktop）。第一次运行会先下载镜像。不连数据库的测试不需要 Docker。

`tests/` 只在本地，不进 git：CI 不跑测试，mypy 只查 `src`。提交前检查是 CI 的检查再加上测试，所以新克隆的仓库要先放好 `tests/`。

启用提交前检查（任何一项失败就拒绝提交）：

```bash
git config core.hooksPath .githooks
```

`lint-imports` 按 `pyproject.toml` 里的 import-linter 契约检查三件事：层与层之间只能向下依赖，模块内部按上面的结构分层，包和包之间没有循环依赖。

import-linter 表达不了的规则写成架构测试，放在 `tests/architecture/`，随 `pytest` 一起运行：模块之外只能 import 模块包根，纯逻辑区域只能用白名单里的包和内置函数，层级包的 `__init__.py` 只放说明，文件之间没有循环 import，不依赖本机时区。每条规则都有用真实源码样例写的正例和负例测试。
