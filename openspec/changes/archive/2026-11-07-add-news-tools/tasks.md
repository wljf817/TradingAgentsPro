# Tasks

## 1. 新闻模块

- [x] 1.1 测试：`tests/fakes/news.py` 的新闻源记录结束时刻、存储加 `article`；`news-and-filings` 的「给出结束时刻」「按新闻 ID 读一条新闻」的场景，含 Postgres 存储
- [x] 1.2 实现：`NewsSource.articles` 的结束时刻、`NewsService.fetch_articles` 的结束时刻和 `article`、`NewsStore.article`、`PostgresNewsStore.article`
- [x] 1.3 测试：`alpaca-news` 的「带结束时刻」和「结束时刻之后发布的新闻」的场景
- [x] 1.4 实现：`AlpacaNewsSource` 的 `end` 参数和过滤
- [x] 1.5 测试：`NewsConfig` 非法值、`news_window`、`NewsLookupService.search` 和 `read`：覆盖 news-tools spec「列出新闻」「补抓」和「读一条新闻」里查不到的场景，用内存存储和假时钟
- [x] 1.6 实现：`modules/news/config.py`、`modules/news/domain/lookup.py`、`NewsLookupService`，包根导出

## 2. 读取工具

- [x] 2.1 测试：`tests/integrations/llm` 用测试服务器覆盖 news-tools spec「新闻工具」「读一条新闻」的去掉 HTML 和没有正文、「列出新闻」的结果格式和说明、「工具返回的新闻可以引用」；引用看交给网关的工具返回过的内容，网关按它检查已有测试
- [x] 2.2 实现：`integrations/llm/news.py` 的 `news_tools`、去掉 HTML、结果的格式和说明，包根导出
- [x] 2.3 更新 `sub-agent-calls` 的测试里用到 `no_tools` 的地方，确认 Fundamentals 还是没有工具

## 3. 配置和装配

- [x] 3.1 测试：配置文件的 `news` 一节（settings-file spec）；bootstrap 给 News 接上两个工具，界线用事件检测的回看时长
- [x] 3.2 实现：`Settings.news`；`_case_side` 把 `news` 提到 `_model_backend` 之前，构造 `NewsLookupService`，`_sub_agent_setup` 接收工具
- [x] 3.3 运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate add-news-tools --strict`

## 4. 文档和冒烟

- [x] 4.1 完整版 Proposal「News」的工具规则：改写补抓那一条，写上默认范围、条数和最早范围、列表返回的新闻可以引用
- [x] 4.2 主机冒烟：提交部署以后，用部署的代码和生产 venv、真实 Alpaca、deepseek-flash 和 Haiku 5.5，内存存储，key 走 ssh stdin，不留东西：两个模型都调用 `list_news`、`read_news` 并交回引用工具内容的信号；没有正文的稿件和 90 天的长范围各看一次
