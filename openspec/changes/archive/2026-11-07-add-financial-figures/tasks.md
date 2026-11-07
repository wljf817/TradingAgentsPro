# Tasks

## 1. 财务事实的存储和按时刻读财报

- [x] 1.1 测试：`tests/fakes/company_data.py` 的财务事实源（记录问到的股票和科目、能回答没有、能报不可用）和内存存储，财报存储加按时刻读；company-data spec「财务事实的记录」「取一只股票的财务事实」「读取财务事实」「财务事实的存储」「按时刻读财报」「公司数据配置」的场景，含 Postgres 存储、同时写入和重启；state-store spec「表的归属和迁移」的新表
- [x] 1.2 实现：`domain/financials.py` 的 `FinancialFact` 和要取的科目；`ports.py`；`config.py` 的 `financial_quarters`；`CompanyDataService` 的 `releases_as_of`、`surprises_as_of`，`FinancialsService` 的 `fetch_facts`、`facts`；`repo.py` 的财务事实表和两个按时刻读；迁移 v0018；用 `uv run pytest tests/modules/companydata tests/bootstrap` 验证

## 2. SEC 的财务事实

- [x] 2.1 测试：sec-filings spec「财务事实」的场景，用替身 SEC：映射、没有 start 的项、不要的科目、代码表里没有、404、只有 IFRS、503、和公告的请求错开
- [x] 2.2 实现：`SecFilingSource.financial_facts`，走同一把锁和间隔，不可用时抛 `CompanyDataUnavailableError`；用 `uv run pytest tests/integrations/sec` 验证

## 3. 季度和报告的规则

- [x] 3.1 测试：`domain/financials.py` 覆盖 financials-tool spec「列出的季度」「每个季度的数字」「代码算的数」「截止」里的场景（基准数据的 F1 到 F21）：结束日和起始日、季度数上限、最早的结束日、直接报告、最近一次提交的版本、全年减前三季度、现金流相减、科目的顺序、EPS 不相减、利润率、自由现金流、同比增长、上年同期亏损、提交日截止；`domain/earnings.py` 的超预期幅度和下一次发布
- [x] 3.2 实现：`domain/financials.py` 的季度划分、取数、相减、截止和代码算的数，`domain/earnings.py` 的超预期幅度和下一次发布，四舍五入到一位小数
- [x] 3.3 测试：`FinancialsService.report` 覆盖 financials-tool spec「补取」「一致预期和实际值」里不涉及模型的场景，用内存存储、假时钟、假财务事实源和假 Finnhub：第一次调用存下事实、SEC 不可用、SEC 不可用也没存过、SEC 没有、一个季度也列不出来、按时刻 T 的版本、没记下过时先刷新、时刻 T 以后才记下、刷新时不可用、Finnhub 上没有、美东日期
- [x] 3.4 实现：`FinancialsService`，美东日期换算，包根导出；用 `uv run pytest tests/modules/companydata` 验证

## 4. 读取工具

- [x] 4.1 测试：`tests/integrations/llm` 用测试服务器覆盖 financials-tool spec「财务数字工具」「每股数字的口径」「结果可以引用」、结果的格式和各条说明；news-tools spec 和 filing-tools spec「工具的说明」
- [x] 4.2 实现：`integrations/llm/financials.py` 的 `financials_tools`、结果的格式和说明；`news.py`、`filings.py` 的说明加一句；包根导出；用 `uv run pytest tests/integrations/llm` 验证

## 5. 装配

- [x] 5.1 测试：bootstrap spec「模拟盘的装配」的「Fundamentals 的读取工具」：Fundamentals 有四个工具，财务事实和公告用同一个 SEC 适配器；配置文件 `companydata` 一节的 `financial_quarters`
- [x] 5.2 实现：`sub_agent_setups` 接收 `FinancialsService`，Fundamentals 的工具合并公告工具和 `financials`；新增 `FinancialsService` 的装配，接上 SEC 适配器和财务事实存储
- [x] 5.3 运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate add-financial-figures --strict`

## 6. 文档和冒烟

- [x] 6.1 完整版 Proposal「Fundamentals」的工具规则：一致预期来自状态库的版本，没记下过时现取并写明；第四季度的 EPS 和股数写缺失；只用快照时刻前一天以前提交的数字；用 `rg` 确认和本变更一致
- [x] 6.2 主机冒烟：提交部署以后，用部署的代码和生产 venv、真实 SEC 和 Finnhub、deepseek-flash 和 Haiku 5.5，内存存储，key 走 ssh stdin，不留东西：两个模型都调用 `financials` 并交回引用 `financials/<股票>` 的信号，deepseek-flash 不再多传 `symbol`；看一次 JPM 的 `financials` 耗时
