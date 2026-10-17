# Tasks

## 1. 人的决定、Proposal 和配置

- [x] 1.1 人对 proposal「需要人决定」表态以后：同意推荐做法的不改；不同意的先改 spec 和 design（第 1 条改 company-data spec「板块」「查一只股票的板块」和 design D9，第 2 条改 spec「板块」的表和 design D2，第 3 条只改 proposal 的 Non-goals）。验证：`openspec validate add-earnings-calendar-and-sector --strict` 通过，proposal、spec、design、tasks 里说法一致
- [x] 1.2 改完整版 Proposal「股票池与寻股」字段表的板块一行，用 proposal「What Changes」里的措辞，最后一句按第 1 条的决定定稿；「技术栈与部署」的数据源一行不动（由「新闻和公告」改）。验证：`grep -n "SPDR" "docs/AI Agent 美股交易系统需求 Proposal.md"` 只有板块这一行；简版没有改动
- [x] 1.3 `pyproject.toml` 的 pytest 配置登记标记 `finnhub_api`（"连真实的 Finnhub，需要 FINNHUB_API_KEY"），`addopts` 的 `-m` 加上 `and not finnhub_api`，上面的注释写上运行方法。验证：`uv run pytest --markers` 列出 `finnhub_api`，`uv run pytest -q` 全部通过

## 2. 板块和财报的类型（domain）

- [x] 2.1 先写测试：`tests/modules/companydata/__init__.py`、`domain/__init__.py`；`domain/test_sector.py` 覆盖 company-data spec「板块」的全部场景；`domain/test_earnings.py` 覆盖发布的键（`AAPL:2026Q4`）和 `SurpriseHistory.of` 按报告期从新到旧排序（design D4）。每个测试的说明写明对应的 spec 场景。验证：确认因为 `tradingagentspro.modules.companydata` 不存在而失败
- [x] 2.2 实现 `src/tradingagentspro/modules/companydata/domain/`：`__init__.py`（说明）、`sector.py`（`SectorEtf`、代码表、`sector_of`，design D2）、`earnings.py`（`ReleaseTime`、`EarningsRelease`、`EarningsSurprise`、`SurpriseHistory`，design D4）。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. 端口、配置和用例

- [x] 3.1 先写测试：
  - `tests/fakes/company_data.py`：`FakeCompanyDataSource`、`InMemoryEarningsStore`（design D10）
  - `tests/modules/companydata/test_service.py`：company-data spec「查一只股票的板块」「财报的记录」「刷新一只股票的财报」「读取财报」的全部场景，以及「公司数据配置」的改窗口；WARNING 日志用 `caplog` 检查，阈值从配置读取，不在测试里硬编码
  - `tests/modules/companydata/test_config.py`：「公司数据配置」的默认配置、配置非法
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `CompanyDataService` 等不存在而失败
- [x] 3.2 实现 `ports.py`（`CompanyDataSource`、`CompanyDataUnavailableError`、`EarningsStore`，design D3、D5）、`config.py`（`CompanyDataConfig`）、`service.py`（`CompanyDataService`，design D6、D9）、`__init__.py`（模块说明和导出，design 的模块边界；`PostgresEarningsStore` 在 4.2 再导出）。验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. 存储和迁移

- [x] 4.1 先写测试（要开着 OrbStack：`open -a OrbStack`）：
  - `tests/modules/companydata/test_repo.py`：`PostgresEarningsStore` 跑 company-data spec「财报的存储」的三个场景，和用真的数据库的「同一个财年季度是同一次发布」（直接查表数版本）；按股票读只给这只股票的、每次发布的最新版本
  - `tests/bootstrap/test_database.py`：「空数据库升级到最新版本」加两张表的列和主键，并用 `PostgresEarningsStore` 保存和读取；「表名以写入它的模块开头」加两张表属于 `companydata`
  - `tests/bootstrap/test_app.py`：表的清单加两张表，最新迁移版本改为 `"0009"`

  验证：确认新断言因为表和 `PostgresEarningsStore` 不存在而失败
- [x] 4.2 实现 `repo.py`（两张表、`PostgresEarningsStore`，design D5），`__init__.py` 导出 `PostgresEarningsStore` 并在说明里写明两张表；迁移 `bootstrap/migrations/versions/v0009_create_company_data.py`（design D10，`down_revision = "0008"`，确认 v0008 已经是「新闻和公告」的迁移）。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. Finnhub 适配器

- [x] 5.1 先写测试：
  - `tests/integrations/finnhub/__init__.py`
  - `tests/integrations/finnhub/payloads.py`：finnhub-company-data spec 基准数据的三个 JSON，函数按需覆盖字段
  - `tests/integrations/finnhub/test_source.py`：`httpx.MockTransport` 记录请求（方法、路径、参数、请求头）、按路径返回；「请求」「行业」「财报日历的映射」「surprise 的映射」「调用失败的分类」的全部场景；类型检查 `source: CompanyDataSource = FinnhubSource(...)`
  - `tests/integrations/finnhub/test_config.py`：「Finnhub 配置」的两个场景
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.integrations.finnhub` 不存在而失败
- [x] 5.2 实现 `src/tradingagentspro/integrations/finnhub/source.py`（`FINNHUB_URL`、`FinnhubConfig`、`FinnhubSource`，design D7）和 `__init__.py`（包说明和导出）。验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 连真实 Finnhub 的冒烟测试

- [x] 6.1 写 `tests/integrations/finnhub/test_finnhub_smoke.py`（design D10 的冒烟测试），全部加 `finnhub_api` 标记，没有 `FINNHUB_API_KEY` 时跳过；整个文件不超过 50 个请求。README 的「常用命令」加一行运行方法，写明需要 `FINNHUB_API_KEY`。验证：没有 key 时 `uv run pytest -m finnhub_api` 全部跳过，`uv run pytest` 不收集这些测试
- [ ] 6.2 人注册 Finnhub 免费 key 以后，运行一次 `FINNHUB_API_KEY=… uv run pytest -m finnhub_api`。验证：全部通过，并且确实运行了（不是跳过）；板块对不上、窗口给不到 90 天、限额不是每分钟 60 次、查不到的股票返回的格式和假设不同时，先改 spec 和 design（代码表、配置默认值、`对后续变更的影响`），再改代码和 2、3、5 组的测试，然后重跑

## 7. 收尾

- [x] 7.1 对照三份 delta spec，逐条列出每个场景由哪个测试覆盖，没有遗漏。验证：对应表写在完成报告里
- [x] 7.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-earnings-calendar-and-sector --strict`。验证：全部通过（要开着 OrbStack）；`git status` 里没有 `tests/` 下的文件被暂存
- [ ] 7.3 归档前：「新闻和公告」先归档、改过 state-store「表的归属和迁移」的，按当时的主 spec 重写本变更的 state-store delta，只加本变更的两张表；确认 v0009 的 `down_revision` 是当时最新的版本、`test_app.py` 的最新版本是 `"0009"`；6.2 已经完成。验证：`openspec validate add-earnings-calendar-and-sector --strict` 通过；归档后的主 spec 里「新闻和公告」加的表和场景都还在
