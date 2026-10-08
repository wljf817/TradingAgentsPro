# Tasks

## 1. 依赖

- [x] 1.1 `uv add "dbos>=3.2.0"`，更新 `uv.lock`（design D8）。验证：`uv run python -c "import dbos"` 成功；`uv run mypy`、`uv run lint-imports` 通过；`uv run pytest` 和加依赖之前一样全部通过

## 2. 环境变量

- [x] 2.1 先写测试 `tests/bootstrap/test_settings.py`：spec「环境变量」的「一次列出全部缺少的变量」（没有和空字符串都算缺少，按 `VARIABLES` 的顺序列出）「Discord 的 ID 不是正整数」「数据库地址写错」（错误信息里没有地址的任何部分）「数据库地址不写驱动」（驱动换成 `postgresql+psycopg`，写了 `+psycopg` 的不变）；另外检查 `repr(settings)` 和 `database_label` 里没有密钥、密码是 `***`。每个测试的说明写明对应的 spec 场景。验证：确认因为 `tradingagentspro.bootstrap.settings` 不存在而失败
- [x] 2.2 实现 `src/tradingagentspro/bootstrap/settings.py`（`VARIABLES`、`SettingsError`、`Settings`、`load_settings`，design D3）。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 3. 日志

- [x] 3.1 先写测试 `tests/bootstrap/test_logs.py`（design D12）：spec「日志」的「时间用 UTC」（构造 `created` 已知的 `LogRecord`，进程在 UTC+14）「httpx 的请求日志不输出」（httpx 和 httpcore 都检查）「DEBUG 不输出」；`dbos` logger 的一条 INFO 经同一个 stream 只出现一次，并且之后再 `DBOS()` 构造也不会加它自己的 handler（只检查 logger 的 handler，不 launch）；异常带异常栈。用夹具在每个测试前后保存和恢复根 logger 与 `dbos`、`httpx`、`httpcore` logger 的 handler、级别和传递设置。验证：确认因为 `tradingagentspro.bootstrap.logs` 不存在而失败
- [x] 3.2 实现 `src/tradingagentspro/bootstrap/logs.py`（`FORMAT`、`UtcFormatter`、`configure_logging`，design D4）。验证：3.1 的测试通过；跑完整个 `uv run pytest`，确认别的测试（尤其是用 `caplog` 的）不受影响；`uv run mypy`、`uv run ruff check` 通过

## 4. 下单流程接上之前

- [x] 4.1 先写测试 `tests/bootstrap/test_order_flow.py`（design D9、D12）：spec「下单流程接上之前」的五个场景。`held_sector` 用返回给定 `Account` 的函数（有 AAPL「科技」、没有 XYZ、读账户抛 `BrokerUnavailableError`）；`/plan` 的回复用真实的 `PlanCommand`、`tests/integrations/discord/calls.py` 的 `FakeCall`、`tests/fakes/ids.py`、样例日历；按钮用真实的 `DiscordNotifier`、`FakeGateway`、`FakePress`，检查提示「没有送达，请再按一次」、消息没有被改。另外检查 `AccountPlanIntake` 满足 `PlanIntake`、`refuse_decision` 满足 `DecisionHandler`（类型检查）。验证：确认因为 `tradingagentspro.bootstrap.order_flow` 不存在而失败
- [x] 4.2 实现 `src/tradingagentspro/bootstrap/order_flow.py`（`OrderFlowMissingError`、`AccountPlanIntake`、`refuse_decision`）。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 启动对账和第一次健康判定

- [x] 5.1 先写测试 `tests/bootstrap/test_startup.py`（design D7、D12）：用 `tests/fakes/` 的券商、止损单、订单和止损保护和交易记录的存储、行情源、`FakeClock`、样例日历建 `ExecutionService`、`HealthService`、`MarketDataService`。覆盖 spec「启动对账」的三个场景（没有不一致写 INFO；来历不明的持仓和不认得的挂单写 WARNING，内容有股票、客户端订单 ID 和股数，返回的结果里有这些不一致，没有撤单）和读取失败时 `BrokerUnavailableError` 原样抛出；spec「第一次健康判定」的四个场景（盘中行情正常、盘中取不到行情、盘外取不到行情、开盘后行情停在前一个交易日收盘），检查传给健康监控的信号按表取值、日志的级别和内容。验证：确认因为 `tradingagentspro.bootstrap.startup` 不存在而失败
- [x] 5.2 实现 `src/tradingagentspro/bootstrap/startup.py`（`reconcile_at_startup`、`judge_health_at_startup`）。验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 装配、启动和停止

- [x] 6.1 先写测试的外部替身 `tests/bootstrap/externals.py`（design D12）：用 `FakeAlpaca` 的 transport 建 `AlpacaClient` 的工厂；OpenD 替身（满足 `QuoteContext`，快照用 `tests/integrations/moomoo/payloads.py` 的 SPY 一行、更新时刻按测试的时刻改写，可以安排报错，记下是否关闭，可以安排关闭时抛错）；返回给定 `FakeGateway` 的 Discord 工厂；`FakeClock`（默认 2026-10-14T14:00:00Z）和 `FakeIds`；以及按基准数据建 `Externals` 和环境变量映射（密钥用只在这里出现的值，数据库地址来自 `empty_database`）的函数。再写 `tests/bootstrap/test_app.py` 的第一部分（要开着 Docker）：先用 `StringIO` 调用 `configure_logging`，`stop` 先设置好再调用 `run`。覆盖 spec「命令行」的「正常停止的退出码」、「启动顺序」两个场景、「数据库」两个场景、「交易日历的加载范围」两个场景（改范围时传 `BootstrapConfig`）、「模拟盘的装配」两个场景（检查 `start` 返回的 `TradingApp` 和 `paper_execution` 的结果；费用用 `FakeAlpaca` 里全部成交的买单和止损单）、「启动对账」的「来历不明的持仓」、「第一次健康判定」的「盘中 OpenD 没开」、「DBOS」、「停止」的「SIGTERM」顺序。验证：确认因为 `tradingagentspro.bootstrap.app` 不存在而失败
- [x] 6.2 写 `tests/bootstrap/test_app.py` 的第二部分：spec「启动失败」的六个场景（数据库连不上用 `127.0.0.1` 上没人监听的端口，不用 Docker；升级失败先手工建一张 `risk_reservations`；Alpaca 401 和超时用 `FakeAlpaca.respond_next`、`raise_next`；对账时连不上安排在读取全部挂单；Discord 用 `FakeGateway.run_error`），每个都检查退出码 1、错误日志写明的步骤、后面的步骤没有做（Alpaca 收到的请求、OpenD 替身是否建过、`dbos` schema 是否存在）、已经打开的部分按相反顺序关闭；「停止」的「某一项关闭出错」「启动过程中收到停止信号」（在 `FakeGateway.run` 里设置 `stop`）；「日志」的「日志里没有密钥」（正常启停、Alpaca 401、数据库连不上三次，检查整个 stream）。验证：确认因为 `tradingagentspro.bootstrap.app` 不存在而失败
- [x] 6.3 `src/tradingagentspro/integrations/discord/__init__.py` 包根导出 `Gateway`（design 的模块边界），add-discord-notify 的 design 模块边界里包根的导出一起补上（单独提交）；实现 `src/tradingagentspro/bootstrap/app.py`（`QuoteContext`、`Externals`、`SYSTEM_EXTERNALS`、`BootstrapConfig`、`TradingApp`、`paper_execution`、装配、`StartupError`、`step`、`on_stop`、`start`、`run`，design D1、D5、D6、D8、D10、D11）。验证：6.1、6.2 的测试通过，每个测试结束后 DBOS 都已经 destroy、没有留下运行中的非守护线程（和测试开始时的 `threading.enumerate()` 比）；`uv run pytest` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 命令行

- [x] 7.1 先改 `tests/bootstrap/test_cli.py`（design D2、D10、D12）：spec「命令行」的「打印版本号」（`main(["--version"])` 输出版本号，`SystemExit` 代码 0，没有调用 `run`）「用法不对」（没有子命令、`start`，代码 2）；`main(["run"])` 把 `os.environ` 和一个 `Event` 交给 `run`（monkeypatch `cli.run`），返回它的退出码，并且装好 SIGTERM、SIGINT 的处理（向本进程发 SIGTERM 以后 `Event` 被设置；测试结束恢复原来的处理和日志设置）。验证：确认新的测试因为还没实现而失败
- [x] 7.2 实现 `src/tradingagentspro/bootstrap/cli.py`：argparse 的 `run` 和 `--version`，`run` 时依次 `configure_logging()`、装信号处理、`return run(os.environ, stop)`。README「常用命令」加上 `uv run tradingagentspro run`：写明要的 7 个环境变量（变量名，不写值）、要开着 PostgreSQL、OpenD 没开时照常启动、Ctrl-C 停止。验证：7.1 的测试通过；`uv run tradingagentspro --version` 打印版本号；`uv run tradingagentspro` 退出码 2；`env -u TRADINGAGENTSPRO_DATABASE_URL -u APCA_API_KEY_ID -u APCA_API_SECRET_KEY -u DISCORD_BOT_TOKEN -u DISCORD_GUILD_ID -u DISCORD_CHANNEL_ID -u DISCORD_USER_ID uv run tradingagentspro run` 在 stderr 列出 7 个变量、退出码 1、没有连接任何东西；`uv run mypy`、`uv run ruff check` 通过

## 8. 收尾

- [x] 8.1 对照 `specs/bootstrap/spec.md`，确认每个场景都有对应的测试，测试名或说明里写明场景；只能在真实环境验证的（真实的 Alpaca 认证、OpenD、Discord 回复、Ctrl-C）写明由 8.3 的冒烟运行覆盖。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 8.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
- [ ] 8.3 由人在自己的终端做冒烟运行（最后一项，需要人操作）：
  - 先准备：部署机器上如果已经用同一个 Discord token 或 Alpaca 账户运行着程序，先停掉；本机 OpenD 开着并已登录；OrbStack 开着
  - 起一个临时的 PostgreSQL，只在本机监听、不用密码：`docker run -d --name tradingagentspro-smoke-db -p 127.0.0.1:5432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:18`（5432 被占用就换一个本机端口，下面的地址跟着改）
  - 在终端里设 `TRADINGAGENTSPRO_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/postgres`；`APCA_API_KEY_ID`、`APCA_API_SECRET_KEY` 和四个 `DISCORD_*` 变量也只设在终端里，不写进仓库里的任何文件
  - 运行 `uv run tradingagentspro run`，确认日志依次出现：程序启动、数据库已升级、交易日历的范围、Alpaca 账户设置完成、启动对账、健康、Discord 已连接、DBOS 已启动、启动完成。数据库是空的，启动对账只读券商（另有一次账户设置）；模拟盘上如果还留着以前冒烟测试的持仓或挂单，会有一条不一致的 WARNING，这是预期的，不会撤单或下单
  - 在 Discord 里运行 `/plan close symbol:XYZ`，回复没有 XYZ 的持仓；运行 `/plan buy symbol:SPY sector:测试 quantity:1 limit:1.00 stop:0.90`，回复提交出错、原因是下单流程还没接上；频道里没有新的确认消息
  - 按 Ctrl-C：日志依次出现收到停止信号、已关闭 DBOS、已关闭 Discord、已关闭 OpenD 行情连接、已关闭数据库连接、已停止，没有异常栈；`echo $?` 是 0
  - 再运行一次，确认数据库已经是最新、照常启动完成，再按 Ctrl-C
  - 运行 `env -u DISCORD_USER_ID uv run tradingagentspro run`：错误日志列出 `DISCORD_USER_ID`，`echo $?` 是 1
  - 可选：退出 OpenD 再启动一次，确认有「取不到行情」的 WARNING、照常启动完成（盘中还会写降级的 WARNING），再按 Ctrl-C
  - 清理：`docker rm -f tradingagentspro-smoke-db`

  验证：全部符合；和 spec 不符的地方先改 spec 和 design，再改代码和 2 到 7 组的测试
