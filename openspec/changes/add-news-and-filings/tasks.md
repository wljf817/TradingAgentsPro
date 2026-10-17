# Tasks

## 1. 人的决定和完整版 Proposal

- [x] 1.1 人对 proposal「需要人决定」表态以后：同意推荐做法的不改；不同意的先改 spec 和 design（第 1 条改 alpaca-news「请求和分页」的 include_content 和 design D2；第 2 条改 sec-filings「submissions 的映射」的"全部表单类型都取"；第 3 条加清理的要求；第 4 条改 sec-filings「股票代码到 CIK」；第 5 条改 news-and-filings「取新闻」「取公告」和两个适配器的失败分类；第 6 条改 news-and-filings「取新闻」和 design D4）。验证：`openspec validate add-news-and-filings --strict` 通过，proposal、spec、design、tasks 里说法一致
- [x] 1.2 按 proposal「What Changes」最后一项的措辞改完整版 Proposal「技术栈与部署」的数据源一行和「架构 · 触发」的数据源一句（design D10）。验证：`grep -n "待定" "docs/AI Agent 美股交易系统需求 Proposal.md"` 不再有数据源一行；简版不改；文档里没有"（原为…）"之类的变更说明

## 2. 条目格式、表和迁移

- [x] 2.1 新建 `src/tradingagentspro/contracts/news.py`：`NewsArticle`、`Filing`（design D2）。验证：`uv run mypy`、`uv run pytest tests/architecture` 通过（纯区域只用了 datetime 和 pydantic）
- [x] 2.2 先写测试（design D4、D8）：`tests/bootstrap/test_database.py` 的「空数据库升级到最新版本」加 `news_articles`、`news_filings` 的列、主键和两个索引，「表名以写入它的模块开头」加两张表属于 `news`；`tests/bootstrap/test_app.py` 的表的清单加两张表，最新迁移版本改为 `"0008"`。验证：要开着 OrbStack（`open -a OrbStack`）；确认新断言因为表不存在而失败
- [x] 2.3 实现：`modules/news/repo.py` 的两个表定义（design D4）；迁移 `bootstrap/migrations/versions/v0008_create_news_tables.py`，写明每一列和索引、不 import 业务代码，`down_revision = "0007"`（design D8）。验证：2.2 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 新闻和公告模块

- [x] 3.1 先写测试（design D3、D9）：
  - `tests/fakes/news.py`：`FakeNewsSource`、`FakeFilingSource`（按调用依次给出安排好的结果，记下每次被问的股票和起始时刻，可以安排下一次抛 `SourceUnavailableError`）、`InMemoryNewsStore`（按键保存，写入只写还没存过的并给出写入的，读取按股票和时刻筛选、排序）
  - `tests/modules/news/test_service.py`：news-and-filings spec「新闻和公告的条目」「取新闻」「取公告」「读一只股票的新闻和公告」的全部场景，用内存存储
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.modules.news` 不存在而失败
- [x] 3.2 实现 `src/tradingagentspro/modules/news/`：`ports.py`（`SourceUnavailableError`、`NewsSource`、`FilingSource`、`NewsStore`）、`service.py`（`NewsService`）、`__init__.py`（模块说明并导出，design 的模块边界）。验证：3.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过
- [x] 3.3 先写测试（design D4、D9）：`tests/modules/news/test_repo.py`：news-and-filings「新闻和公告的存储」的全部场景用 `PostgresNewsStore` 和真的数据库跑（原样读回时进程在 UTC+14；写入时刻直接查表；「同时取到同一条」用两个线程和 `threading.Barrier`；「读写出错」用连不上的地址、没升级的空数据库、手工写进一行缺标题的 data）；`PostgresNewsStore` 的读取顺序、空的写入不访问数据库；`test_database.py` 的「空数据库升级到最新版本」补上用 `PostgresNewsStore` 追加和读取。验证：要开着 OrbStack；确认因为 `PostgresNewsStore` 不存在而失败
- [x] 3.4 实现 `repo.py` 的 `PostgresNewsStore`（design D4），`__init__.py` 导出它，说明写明新闻和公告存在哪两张表。验证：3.3 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. Alpaca 新闻源

- [x] 4.1 先写测试（design D5、D9）：
  - `tests/integrations/alpaca/news_payloads.py`：alpaca-news spec 基准数据的两条新闻，组成 Alpaca 回复的 JSON，函数按需覆盖字段和下一页标记
  - `tests/integrations/alpaca/test_news.py`：用 `httpx.MockTransport` 替身 Alpaca，记下每个请求的地址、参数和请求头；「请求和分页」「字段的映射」「起始时刻之前发布的新闻」「新闻请求失败的分类」「Alpaca 新闻配置」的全部场景；类型检查 `source: NewsSource = AlpacaNewsSource(...)`
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `integrations/alpaca/news.py` 不存在而失败
- [x] 4.2 实现 `src/tradingagentspro/integrations/alpaca/news.py`（`DATA_URL`、`AlpacaNewsConfig`、`AlpacaNewsSource`，design D5），`integrations/alpaca/__init__.py` 导出它们并在说明里写上新闻源。验证：4.1 的测试通过，测试进程在 UTC+14 时区下发布时刻和 start 仍然正确；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 5. SEC EDGAR 公告源

- [x] 5.1 先写测试（design D6、D7、D9）：
  - `tests/integrations/sec/__init__.py`
  - `tests/integrations/sec/payloads.py`：sec-filings spec 基准数据里的代码表四项和 AAPL submissions 的四行（2026-10-17 从 SEC 录下，保留映射用到的列），函数组成按列存放的 submissions，按需加行、换 CIK
  - `tests/integrations/sec/test_edgar.py`：替身 SEC 按地址给出回复，记下每个请求的地址、User-Agent 和先后；`pause` 换成记录函数，等待时长从配置算出；「股票代码到 CIK」「submissions 的映射」「受理时刻的换算」「同一份公告列在几家公司下」「User-Agent 和请求间隔」「公告请求失败的分类」「SEC 配置」的全部场景（「两个调用同时取」用两个线程，替身处理请求时检查没有别的请求正在处理）；类型检查 `source: FilingSource = SecFilingSource(...)`
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.integrations.sec` 不存在而失败
- [x] 5.2 实现 `src/tradingagentspro/integrations/sec/edgar.py`（`SecConfig`、`SecFilingSource`、`_accepted_at`、`_wait`，design D6、D7）和 `__init__.py`（包说明并导出）。验证：5.1 的测试通过，测试进程在 UTC+14 时区下受理时刻仍然正确；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 6. 连真实服务的冒烟测试

- [x] 6.1 `pyproject.toml` 的 pytest 配置登记标记 `news_sources`（连真实 Alpaca 新闻和 SEC EDGAR，需要 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY`、`SEC_USER_AGENT`），`addopts` 的 `-m` 加 `and not news_sources`。写 `tests/integrations/alpaca/test_news_smoke.py` 和 `tests/integrations/sec/test_edgar_smoke.py`（design D9），全部加 `news_sources` 标记，各自缺环境变量时跳过；测试和代码里不写任何具体邮箱。README 的「常用命令」加一行运行方法和要的环境变量。验证：没有这些环境变量时 `uv run pytest -m news_sources` 全部跳过，`uv run pytest` 不收集这些测试
- [ ] 6.2 人设好 `SEC_USER_AGENT` 以后运行一次 `uv run pytest -m news_sources`，同时用真实的 key 录一份 Alpaca 新闻回复核对 `news_payloads.py` 的字段和可以为 null 的项。验证：全部通过；和 spec 不符的地方（比如 url 从不为 null、受理时刻的偏差不再存在）先改 spec 和 design，再改代码和 4、5 组的测试

## 7. 收尾

- [x] 7.1 对照本变更的四份 delta spec，逐条列出每个场景由哪个测试覆盖，没有遗漏；state-store「表的归属和迁移」原有的场景仍有测试。验证：对应表写在完成报告里
- [x] 7.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-news-and-filings --strict`。验证：全部通过（要开着 OrbStack）；`git status` 里没有 `tests/` 下的文件被暂存
- [ ] 7.3 归档前：「财报日历与板块」如果先归档、改过 state-store「表的归属和迁移」，按当时的主 spec 重写本变更的 delta，只加本变更的两张表；确认 v0008 的 `down_revision` 是 `"0007"`、`test_app.py` 的最新版本和当时的最新迁移一致。验证：`openspec validate add-news-and-filings --strict` 通过；归档后的主 spec 里别的变更加的表都还在
