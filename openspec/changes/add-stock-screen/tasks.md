# Tasks

## 1. 人的决定、前提、Proposal 和配置

- [x] 1.1 按人的决定改 proposal「需要人决定」（写成已定，末尾「由人决定」）、stock-screen spec（上升趋势是前提，三个条件是相对强度、回调、成交量异常，粗筛结果记下在上升趋势里的只数）、design D7（相对强度前百分比的分母）；state-store delta 接在「财报日历与板块」的 delta 上。验证：`openspec validate add-stock-screen --strict` 通过，proposal、spec、design、tasks 里说法一致
- [x] 1.2 确认前提：「财报日历与板块」已经 apply（`modules/companydata` 导出 `CompanyDataService`，最新迁移是 v0009；「新闻和公告」的 `DATA_URL` 已经在 `integrations/alpaca/news.py`）。验证：`ls src/tradingagentspro/bootstrap/migrations/versions/` 的最后一个是 v0009，`uv run python -c "from tradingagentspro.modules.companydata import CompanyDataService"` 成功
- [x] 1.3 改完整版 Proposal 两处，用 proposal「What Changes」里的措辞：「股票池与寻股」寻股流程那一句去掉「盈利预期上调、」；「技术栈与部署」的数据源一行，moomoo 那一项补上条件选股和股票列表。`contracts/listing.py` 日均成交额的注释改为"日均成交额，美元，算法见 stock-screen spec「日均成交额」"，字段不变。验证：`grep -n "盈利预期上调" "docs/AI Agent 美股交易系统需求 Proposal.md"` 没有结果，`grep -n "条件选股"` 只有数据源一行；简版没有改动
- [x] 1.4 `pyproject.toml` 的 pytest 配置登记标记 `alpaca_bars`（"连真实的 Alpaca 市场数据取日线，需要 APCA_API_KEY_ID 和 APCA_API_SECRET_KEY"），`addopts` 的 `-m` 加上 `and not alpaca_bars`，上面的注释写上运行方法。验证：`uv run pytest --markers` 列出 `alpaca_bars`，`uv run pytest -q` 全部通过

## 2. 粗筛的纯逻辑（domain 和配置）

- [x] 2.1 先写测试：
  - `tests/modules/screener/__init__.py`、`domain/__init__.py`
  - `tests/modules/screener/baseline.py`：stock-screen spec 的场景配置和基准数据，函数按需覆盖收盘价、成交量、成交均价、缺哪天
  - `domain/test_indicators.py`：「日均成交额」「相对强度」「上升趋势」「回调」「成交量异常」里能在 domain 算的场景（日均成交额 124,000,000、82,800,000，相对强度，两种不在上升趋势，回调幅度四个边界，量比边界）
  - `domain/test_screen.py`：截止交易日和交易日窗口（日线延迟的边界、盘中、周六、H 取最大的一项）；「粗筛的股票范围」（范围外、缺日线、流动性门槛的边界、允许 ETF）；相对强度的前百分比和并列（分母包括不在上升趋势里的）；「得分和排序」（基准排序、不在上升趋势里的不排、分数和相对强度相同按代码、和数据源顺序无关）；基准缺日线报错写明 SPY 和 2026-10-12
  - `tests/modules/screener/test_config.py`：「粗筛配置」的配置非法
  - 阈值从配置读取，不在测试里硬编码；每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.modules.screener` 不存在而失败
- [x] 2.2 实现 `src/tradingagentspro/modules/screener/config.py`（`ScreenerConfig`）、`domain/__init__.py`、`domain/indicators.py`、`domain/screen.py`（design D4、D6、D7）。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过（纯区域只用白名单里的标准库）

## 3. 股票池：现在不能加入的股票

- [x] 3.1 先写测试：`tests/modules/stockpool/test_service.py` 加 stock-pool spec「现在不能加入的股票」除"数据库连不上"以外的场景；`tests/modules/stockpool/test_repo.py`（要开着 OrbStack：`open -a OrbStack`）加"数据库连不上"。验证：确认因为 `StockPoolService.unavailable` 不存在而失败
- [x] 3.2 实现 `StockPoolService.unavailable()`（design D5），模块说明和 `__init__.py` 不变。验证：3.1 的测试和 `tests/modules/stockpool/` 原有的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 4. 端口和用例

- [x] 4.1 先写测试：
  - `tests/fakes/screen.py`：`FakeSecuritySource`、`FakeBarSource`、`FakeSectorSource`、`InMemoryScreenStore`（design D13）
  - `tests/modules/screener/test_service.py`：stock-screen spec「截止交易日」（日线请求的日期范围）、「粗筛的股票范围」（市值下限交给数据源、范围外的不取日线、合格数）、「前 N 只」「粗筛的结果」「数据源不可用」「组装上市信息」的全部场景，用内存存储
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `ScreenerService` 等不存在而失败
- [x] 4.2 实现 `ports.py`（design D3）、`service.py`（`ScreenerService`，design D4、D5、D8、D12）、`__init__.py`（模块说明和导出，design 的模块边界；`PostgresScreenStore` 在 5.2 再导出）。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过；类型检查 `sectors: SectorSource = CompanyDataService(...)` 通过

## 5. 存储和迁移

- [x] 5.1 先写测试（要开着 OrbStack）：
  - `tests/modules/screener/test_repo.py`：`PostgresScreenStore` 跑 stock-screen spec「粗筛结果的存储」的三个场景，以及用真的数据库跑「同一天再粗筛」（直接查表数版本）
  - `tests/bootstrap/test_database.py`：「空数据库升级到最新版本」加 `screener_runs` 的列和主键，并用 `PostgresScreenStore` 保存和读取；「表名以写入它的模块开头」加 `screener_runs` 属于 `screener`
  - `tests/bootstrap/test_app.py`：表的清单加 `screener_runs`，最新迁移版本改为 `"0010"`

  验证：确认新断言因为表和 `PostgresScreenStore` 不存在而失败
- [x] 5.2 实现 `repo.py`（`RUNS`、`PostgresScreenStore`，design D9），`__init__.py` 导出 `PostgresScreenStore` 并在说明里写明表；迁移 `bootstrap/migrations/versions/v0010_create_screener_runs.py`（design D13，`down_revision = "0009"`）。验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. moomoo 适配器

- [x] 6.1 先写测试：
  - `tests/integrations/moomoo/securities_payloads.py`：moomoo-securities spec 的基准数据，按 SDK 的形状（`FilterStockData` 那样有 `stock_code` 的对象、静态信息的 DataFrame 列）
  - `tests/integrations/moomoo/test_securities.py`：假的 `SecuritiesContext` 记下每次调用，`pause` 换成记录函数；「条件选股的请求和分页」「静态信息的映射」「一只股票的上市信息」「请求间隔」「请求失败」的全部场景；类型检查 `source: SecuritySource = MoomooSecuritySource(...)`
  - 「moomoo 股票列表配置」的配置非法
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `MoomooSecuritySource` 不存在而失败
- [x] 6.2 实现 `src/tradingagentspro/integrations/moomoo/securities.py`（design D10），`integrations/moomoo/__init__.py` 的说明和导出加上它。验证：6.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. Alpaca 日线适配器

- [x] 7.1 先写测试：
  - `tests/integrations/alpaca/bars_payloads.py`：alpaca-bars spec 的基准数据（JSON 文本），函数按需覆盖字段
  - `tests/integrations/alpaca/test_bars.py`：`httpx.MockTransport` 记录请求（路径、参数、请求头）、按 symbols 和 page_token 返回；「请求和分页」「日线的映射」「日线请求失败的分类」的全部场景；类型检查 `source: BarSource = AlpacaBarSource(...)`
  - 「Alpaca 日线配置」的超时时长非法
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `AlpacaBarSource` 不存在而失败
- [x] 7.2 实现 `src/tradingagentspro/integrations/alpaca/bars.py`（design D11），`integrations/alpaca/__init__.py` 的说明和导出加上它。验证：7.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 8. 冒烟测试

- [x] 8.1 写 `tests/integrations/moomoo/test_securities_smoke.py`（`moomoo_opend` 标记，本机没开 OpenD 时跳过）、`tests/integrations/alpaca/test_bars_smoke.py`（`alpaca_bars` 标记，缺环境变量时跳过）、`tests/integrations/test_stock_screen_smoke.py`（两个标记都带，缺一个就跳过），内容见 design D13。README「常用命令」加运行方法和需要的环境。验证：没有 OpenD 和 key 时 `uv run pytest -m "moomoo_opend or alpaca_bars"` 里这几个文件全部跳过，`uv run pytest` 不收集它们
- [ ] 8.2 开着 OpenD、设好 Alpaca 的 key，运行一次 `uv run pytest -m "moomoo_opend or alpaca_bars"`。验证：这几个文件全部通过并且确实运行了（不是跳过）；美股覆盖、市值单位、ADR 和 ETF 的识别、查不到的代码、静态信息一批 200 个、Alpaca 日线的时间戳和拆股调整、不带 end 时的权限、空结果的格式和假设不同时，先改 spec 和 design，再改代码和 6、7 组的测试，然后重跑；把完整粗筛打印的请求次数和耗时写进完成报告

## 9. 收尾

- [x] 9.1 对照五份 delta spec，逐条列出每个场景由哪个测试覆盖，没有遗漏（「粗筛配置」的默认值按测试约定不写测试，对照 `config.py` 核对）。验证：对应表写在完成报告里
- [x] 9.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-stock-screen --strict`。验证：全部通过（要开着 OrbStack）；`git status` 里没有 `tests/` 下的文件被暂存
- [ ] 9.3 归档前：「新闻和公告」「财报日历与板块」先归档（本变更的 state-store delta 已经包含它们的表）；确认 v0010 的 `down_revision` 是当时最新的版本、`test_app.py` 的最新版本是 `"0010"`；8.2 已经完成。验证：`openspec validate add-stock-screen --strict` 通过；归档后的主 spec 里前两个变更加的表和场景都还在
