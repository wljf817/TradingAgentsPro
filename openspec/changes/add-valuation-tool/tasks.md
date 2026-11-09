# Tasks

## 1. Alpaca 的原始收盘价和拆股记录

- [x] 1.1 测试：`tests/integrations/alpaca/test_bars.py` 用替身服务器覆盖 alpaca-bars spec「原始收盘价」「拆股记录」「原始收盘价和拆股记录的失败」的场景：请求参数、映射和去掉范围外的日子、分页、没有日线、正向和反向拆股、空对象、拆股分页、超时、429、503、401、缺字段；`test_bars_smoke.py` 加真实 Alpaca 的一条：NVDA 的拆股记录有 2024-06-10 比例 10，原始收盘价 2024-06-07 比 2024-06-10 高约 10 倍
- [x] 1.2 实现：`modules/companydata` 的 `DailyClose`、`Split` 和 `PriceSource`、`SplitSource` 端口（包根导出）；`AlpacaBarSource.daily_closes` 和 `splits`，失败抛 `CompanyDataUnavailableError`；用 `uv run pytest tests/integrations/alpaca` 验证

## 2. 估值的规则

- [x] 2.1 测试：`tests/modules/companydata/domain/test_valuation.py` 用 valuation-tool spec 的基准数据（E1 到 W5、1 拆 2、七天的收盘价）覆盖「TTM」「拆股换算」「PE、PS 和市值」「太旧的数字」「亏损」「历史位置」「截止」里不涉及数据源的场景：全年加今年累计减去年同期累计、没有今年累计、没有去年同期累计、时长差 10 天和 11 天、营收科目跳过太旧的、拆股以前提交的换算、反向拆股、快照以后的拆股、股数取时长最短的、没有股数、营收不大于 0、太旧的一天、正好到上限和超过上限、现在亏损、历史上亏损的一天、基准的位置、拆股那天不跳、每天只用以前提交的、一天也用不上、第二天就能用；阈值从 config 读
- [x] 2.2 实现：`domain/valuation.py` 的 TTM、拆股换算、PE、PS、市值、太旧、亏损和历史位置，`ValuationReport`；舍入按 spec；用 `uv run pytest tests/modules/companydata/domain` 验证
- [x] 2.3 测试：`ValuationService.report` 用内存存储、假财务事实源、假价格源和假拆股源覆盖 valuation-tool spec「价格」「补取和未覆盖」「Alpaca 的请求和不可用」和「历史位置」里窗口的场景：快照的最新价、行情没有最新价、没有行情、第一次调用存下事实、SEC 不可用、SEC 没有、一个季度也列不出来（Alpaca 都没收到请求）、两个请求的参数、收盘价不可用、拆股记录不可用、窗口的年数、2 月 29 日、美东日期；company-data spec「公司数据配置」的配置非法
- [x] 2.4 实现：`config.py` 的 `valuation_years`、`max_figures_age_days`；`ValuationService`，美东日期和窗口的换算，取事实用 `FinancialsService.fetch_facts` 和 `facts`；包根导出；`tests/fakes/company_data.py` 加价格源和拆股源；用 `uv run pytest tests/modules/companydata` 验证

## 3. 读取工具

- [x] 3.1 测试：`tests/integrations/llm/test_valuation_tools.py` 用测试服务器覆盖 valuation-tool spec「估值工具」「结果的说明」「结果可以引用」、从快照里找任务股票的行情、结果的格式（PE 是数、`"亏损"` 或 `null`，位置的各项）和各条说明；sub-agent-calls spec「工具返回过的内容」现有场景照常通过
- [x] 3.2 实现：`integrations/llm/valuation.py` 的 `valuation_tools`、结果的格式和说明，说明用 `STOCK_IS_SET`；包根导出；用 `uv run pytest tests/integrations/llm` 验证

## 4. 装配

- [x] 4.1 测试：bootstrap spec「模拟盘的装配」的「Fundamentals 的读取工具」：Fundamentals 有五个工具，原始收盘价、拆股记录和寻股的日线是同一个 `AlpacaBarSource`；配置文件 `companydata` 一节的两个新项
- [x] 4.2 实现：`sub_agent_setups` 接收 `ValuationService`，Fundamentals 的工具合并公告工具、`financials` 和 `valuation`；`_case_side` 只建一个 `AlpacaBarSource`，交给寻股粗筛和 `ValuationService`
- [x] 4.3 运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate add-valuation-tool --strict`

## 5. 文档和冒烟

- [x] 5.1 完整版 Proposal「Fundamentals」：`valuation` 一行写上价格的来源和过去 5 年的位置；工具规则里「PE 用最近四个季度的 EPS」改成全年加今年累计减去年同期累计，补上最近一期太旧不用、拆股按除权日换算、价格和拆股记录每次向 Alpaca 取不存；用 `rg` 确认和本变更一致
- [ ] 5.2 主机冒烟：提交部署以后，用部署的代码和生产 venv、真实 SEC 和 Alpaca、deepseek-flash 和 Haiku 5.5，内存存储，key 走 ssh stdin，不留东西：两个模型都调用 `valuation` 并交回引用 `valuation/<股票>` 的信号；看 JPM 和 NVDA 的耗时，NVDA、WMT 的 PE 和 Finnhub 的 `peTTM` 差在 3% 以内，拆股那天没有跳变
