# Tasks

## 1. 公告的存储和取公告

- [x] 1.1 测试：`tests/fakes/news.py` 的公告源记录问到的范围、能给出文件，存储加 `filing`、`documents`、`add_documents`；news-and-filings spec「取公告」的结束时刻和表单类型、「按 accession number 读一份公告」「公告的文件」「新闻和公告的存储」的公告文件场景，含 Postgres 存储和同时写入；state-store spec「表的归属和迁移」的新表
- [x] 1.2 实现：`contracts/news.py` 的 `FilingDocument`；`ports.py`；`NewsService.fetch_filings` 的结束时刻和表单类型、`filing`、`documents`；`repo.py` 的公告文件表；迁移 v0017；用 `uv run pytest tests/modules/news tests/bootstrap` 验证

## 2. SEC 的公告文件

- [x] 2.1 测试：sec-filings spec「公告的文件」和「User-Agent 和请求间隔」的场景，用替身 SEC：目录页的文件表、要读哪些文件、转成纯文本、`.txt`、失败的分类
- [x] 2.2 实现：`SecFilingSource.documents`，用 `html.parser` 读目录页和转纯文本，请求走同一把锁和间隔；用 `uv run pytest tests/integrations` 验证

## 3. 查询的规则和服务

- [x] 3.1 测试：`FilingConfig` 非法值；`domain/filings.py` 的能读的表单、范围、筛选和条数、按位置切文字、查找（大小写、’、起点、上限、所在行截 300 字）；`domain/transcripts.py` 的范围、哪条算文字稿、取最新一篇
- [x] 3.2 实现：`modules/news/config.py` 的 `FilingConfig`，`domain/filings.py`、`domain/transcripts.py`
- [x] 3.3 测试：`FilingLookupService` 覆盖 filing-tools spec「列出公告」「公告的补抓」「读公告」「读电话会文字稿」里不涉及模型的场景，用内存存储、假时钟、假公告源和新闻源、给定的股票池判断
- [x] 3.4 实现：`FilingLookupService` 的 `search`、`read`、`find`、`transcript`，美东日期换算，包根导出；用 `uv run pytest tests/modules/news` 验证

## 4. 读取工具

- [x] 4.1 测试：`tests/integrations/llm` 用测试服务器覆盖 filing-tools spec「公告工具」「工具返回的内容可以引用」、三个工具的结果格式和说明、文字稿去掉 HTML；news-tools spec「新闻工具」改过的 Fundamentals 场景
- [x] 4.2 实现：`integrations/llm/filings.py` 的 `filing_tools`、结果的格式和说明，复用 `integrations/llm/news.py` 去掉 HTML 的函数，包根导出；用 `uv run pytest tests/integrations/llm` 验证

## 5. 配置和装配

- [x] 5.1 测试：配置文件的 `filings` 一节（settings-file spec）；bootstrap 给 Fundamentals 接上三个工具，补抓的界线用事件检测的回看时长，股票池判断用装配好的股票池
- [x] 5.2 实现：`Settings.filings`；`sub_agent_setups` 接收 `FilingLookupService`；新增 `filing_lookup` 装配
- [x] 5.3 运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate add-filing-tools --strict`

## 6. 文档和冒烟

- [x] 6.1 完整版 Proposal「Fundamentals」：工具表和工具规则改成按位置读和查找、只列能读的表单、20-F 和 6-K、文字稿的找法；用 `rg` 确认没有剩下「按章节读」
- [ ] 6.2 主机冒烟：提交部署以后，用部署的代码和生产 venv、真实 SEC 和 Alpaca、deepseek-flash 和 Haiku 5.5，内存存储，key 走 ssh stdin，不留东西：两个模型都调用三个工具并交回引用工具内容的信号；看一次 JPM 的 `list_filings` 和一份 10-K 第一次读的耗时
