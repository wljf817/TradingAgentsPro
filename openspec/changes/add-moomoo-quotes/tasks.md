# Tasks

## 1. Proposal 和依赖

- [x] 1.1 按人对 proposal「需要人决定」第 1 条的决定修改完整版 Proposal：同意推荐做法时，把「技术栈与部署」券商一行的"用 asyncio 订阅实时推送和到价提醒推送作为行情事件源"改为"订阅实时推送和到价提醒推送作为行情事件源"。验证：`grep -n asyncio "docs/AI Agent 美股交易系统需求 Proposal.md"` 的结果和人的决定一致；简版不改
- [x] 1.2 `uv add "moomoo-api>=10.11.7108"`；`pyproject.toml` 的 mypy 配置对 `moomoo`、`moomoo.*`、`pandas`、`pandas.*` 设 `ignore_missing_imports = true`（design D6），pytest 配置登记标记 `moomoo_opend`（连本机 OpenD 的冒烟测试），`addopts` 改为 `-m "not alpaca_paper and not moomoo_opend"`（design D8）。验证：`uv run pytest -q` 全部通过，`uv run pytest --markers` 列出 `moomoo_opend`，`uv run mypy` 通过

## 2. 行情模块

- [x] 2.1 先写测试：
  - `tests/fakes/quotes.py`：`FakeQuoteSource`（design D8：按股票保存行情，记录每次被请求的股票，可以安排下一次调用抛 `QuoteUnavailableError`）
  - `tests/modules/marketdata/test_service.py`：market-data spec「取行情」「行情最近一次更新的时刻」的全部场景，以及「行情配置」的改参考股票
  - `tests/modules/marketdata/test_config.py`：「行情配置」的默认配置和参考股票为空
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.modules.marketdata` 不存在而失败
- [x] 2.2 实现 `src/tradingagentspro/modules/marketdata/`：`domain/__init__.py`、`domain/quote.py`（`Quote`，design D2）、`ports.py`（`QuoteSource`、`QuoteUnavailableError`）、`config.py`（`MarketDataConfig`）、`service.py`（`MarketDataService`，design D3）、`__init__.py`（模块说明并导出，design 的模块边界）。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. moomoo 行情源

- [x] 3.1 先写测试：
  - `tests/integrations/moomoo/__init__.py`
  - `tests/integrations/moomoo/payloads.py`：moomoo-quotes spec 基准数据里的四行快照，函数按需覆盖字段，组成和 SDK 返回类型一致的 DataFrame（design D8）
  - `tests/integrations/moomoo/test_quotes.py`：`FakeOpenD`（满足 `SnapshotContext`，记录收到的代码列表，返回安排好的 DataFrame 或错误）；「请求」「快照的映射」「查不到的股票」「调用失败」的全部场景；类型检查 `source: QuoteSource = MoomooQuoteSource(...)`
  - `tests/integrations/moomoo/test_connection.py`：「连接配置」的默认连接配置、连接配置非法；OpenD 没开（design D8：用真的 SDK 连一个没有服务的端口，超时 1 秒，构造和取行情都在线程里跑，`join(timeout=5)` 后线程已结束，取行情报行情不可用，最后 `close()`）
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.integrations.moomoo` 不存在而失败
- [x] 3.2 实现 `src/tradingagentspro/integrations/moomoo/quotes.py`（`MoomooConfig`、`SnapshotContext`、`MoomooQuoteSource`、`open_quote_context`，design D5、D6）和 `__init__.py`（包说明并导出）。验证：3.1 的测试通过，测试进程在 UTC+14 时区下更新时刻仍然正确；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 4. 连本机 OpenD 的冒烟测试

- [x] 4.1 写 `tests/integrations/moomoo/test_opend_smoke.py`（design D8），全部加 `moomoo_opend` 标记，本机 127.0.0.1:11111 连不上时跳过；用系统时钟和纽交所日历判断时段，分为任何时候、常规交易时段内、开盘前 30 分钟内三组，不在对应时段时跳过那一组。README 的「常用命令」加一行运行方法，写明要先开着 OpenD 并登录。验证：OpenD 没开时 `uv run pytest -m moomoo_opend` 全部跳过，`uv run pytest` 不收集这些测试
- [ ] 4.2 本机 OpenD 登录着的时候，在常规交易时段内运行一次 `uv run pytest -m moomoo_opend`，再在某个交易日开盘前 30 分钟内运行一次。验证：两次都通过，并且对应时段那一组确实运行了（不是跳过）；和 spec 不符的地方先改 spec 和 design，再改代码和 2、3 组的测试

## 5. 收尾

- [x] 5.1 对照两份 spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 5.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`，全部通过

## 6. 量比

- [ ] 6.1 先写测试再实现量比（design D9）：
  - 测试：`tests/modules/marketdata/test_service.py` 照 market-data spec「取行情」的基准数据和场景加量比；`tests/integrations/moomoo/test_quotes.py` 照 moomoo-quotes spec「快照的映射」的夏令时的快照、开盘后还没有成交（没有量比）、量比精确读取；`payloads.py` 的行加 `volume_ratio`；`tests/fakes/quotes.py` 的行情带量比。确认因为 `Quote.volume_ratio` 不存在而失败
  - 实现：`modules/marketdata/domain/quote.py` 的 `Quote.volume_ratio`，`integrations/moomoo/quotes.py` 映射 `volume_ratio`；已有用到 `Quote` 的测试补上量比
  - 核对：常规交易时段里在真实 OpenD 上录一次 SPY、AAPL 的快照，确认有 `volume_ratio` 列、有成交时是正数、和 moomoo App 的量比一致；录到的一行补进 `payloads.py`，结论写进本条的完成说明。字段不存在或含义不同就停下，报告给人
  - 验证：上面的测试和原有的行情、快照、紧急退出、下单流程测试通过；`uv run mypy`、`uv run ruff check` 通过
