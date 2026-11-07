# Design

## Context

`LlmBackend.sub_agent` 已经支持按子 agent 注册读取工具：`SubAgentSetup.tools` 收到请求和记录 ID 的函数，给出这次运行的 `ReadTool`。现在两个子 agent 都用 `no_tools`。新闻存在 `news_articles`，事件检测每 60 秒取监控股票最近 24 小时的新闻，把新存下的判断成事件。

## Goals / Non-Goals

**Goals:**

- News 用工具查任务股票快照时刻以前的新闻，模型改不了股票和时刻
- 工具补抓旧新闻，不影响事件检测

**Non-Goals:**

- 见 proposal 的 Non-goals

## Decisions

### D1：模块边界

涉及 `modules/news`、`integrations/alpaca`、`integrations/llm`、`bootstrap`。

| 位置 | 内容 |
| --- | --- |
| `modules/news/config.py` | `NewsConfig`：`max_tool_rows`、`tool_default_days`、`tool_max_days` |
| `modules/news/domain/lookup.py` | `news_window` 算范围，`NewsSearch`（frozen dataclass）是查询结果：新闻、总数、裁到的时刻、范围是否为空、不全的时段 |
| `modules/news/service.py` | `NewsLookupService`：`search`、`read`；`NewsService.fetch_articles` 加可选的结束时刻，加 `article(id)` |
| `integrations/llm/news.py` | `news_tools(lookup)` 给出 `ToolBuilder`；去掉 HTML；结果的格式；说明的文字 |

模块之间传 `SubAgentRequest`（已有）、`NewsArticle`（已有）和 `NewsSearch`。工具结果的格式只在 `integrations/llm/news.py` 里序列化给模型，不进 `contracts`。

### D2：单独一个查询类

`NewsLookupService` 持有 `NewsService`、`Clock`、`NewsConfig` 和事件检测的回看时长，不往 `NewsService` 的构造函数加参数：事件检测和快照不需要这些，它们的装配和测试不用改。回看时长由 bootstrap 从 `settings.event_detection.lookback` 传入，不另设一项配置，两个值就不会对不上。

### D3：不做覆盖表

范围里早于界线的部分每次都问新闻源，写入按新闻 ID 幂等。覆盖表要记下每只股票查过哪些时段，还要处理空洞；补抓的代价只是几页请求。2026-11-06 的测试里 NVDA 120 天 45 页，所以 90 天大约 33 页，实际耗时在主机冒烟时看一次。同一次运行里重复的范围也不记忆，上限 5 次。

### D4：列表沿用现有的读取

`list_news` 用 `NewsStore.articles(symbol, since)` 读出全部，在 Python 里按结束时刻、关键词筛，截最新的若干条。按已有的量推算，NVDA 90 天约 1,900 条、全文约 10 MB，一次运行最多 5 次，不值得加一套不读正文的 SQL。`NewsStore` 只新增 `article(id)`。

### D5：新闻源的结束时刻

`NewsSource.articles` 加 `until: datetime | None = None`，事件检测不传，行为不变。2026-11-06 用真实接口确认 Alpaca 的 `end` 包含这一刻，并且精确到秒：`end` 等于一条新闻的 `created_at` 时能取到它。适配器发送结束时刻舍去秒以下再加 1 秒，回来以后去掉晚于结束时刻的。

### D6：去掉 HTML 放在工具这一层

用标准库 `html.parser`。它不在纯区域的白名单里，放 `integrations/llm/news.py` 不用改白名单。「有没有正文」和 `read_news` 用同一个函数，列表说有、读到没有的情况不会出现。

### D7：条目 ID 和查不到

条目 ID 是 `news/<新闻 ID>`，不带 `s<版本>/`，不会和快照条目相同。`read_news` 的参数是字符串，格式不对和查不到走同一个结果：参数校验失败会让这次运行变成输出不合法，模型写错一个 ID 不值得整个任务失败；写法相同也不透露快照之后有没有这条。

### D8：美东日期在 service 里换算

domain 不能用 `zoneinfo`，所以 `NewsLookupService` 把美东日期换成时刻，再交给 `news_window`。结束日的结束取下一天美东 00:00 减 1 微秒，后面的比较都含边界。

### D9：补抓的影响

界线以前的新闻事件检测已经不再取，工具写入不会让它把新闻当成处理过的。补抓到的新闻落在快照的回看范围内时，之后的快照和 PM 的标题列表里会出现它们，这只让内容更全；已经生成的快照不变。

### 省略的情况

- 事件检测还没轮到的最新新闻，工具看不到，最多差一轮
- 股票不在股票池时最近 24 小时没有人写入：寻股不派 News，案件中途移出股票池的情况不处理
- 新闻源后来改过的稿件，已经存下的不更新
- 模型超时以后还在跑的工具线程继续写入：写入幂等，不取消
- 工具调用不写程序日志：内容已经在模型调用留痕里

## Risks / Trade-offs

- 几个任务同时补抓长范围可能碰到 Alpaca 每分钟 200 次的限制 → 这次写明可能不全，事件检测用同一个额度，下一轮再取
- 工具结果占用输入：50 条大约 13k 字，业绩会实录全文 30k 到 60k 字 → 费用不是约束，上线以后看实际用量再调配置
