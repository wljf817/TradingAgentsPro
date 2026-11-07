# Design

## Context

`LlmBackend.sub_agent` 按子 agent 注册读取工具，News 已经用 `news_tools`，Fundamentals 还是 `no_tools`。公告存在 `news_filings`，只有表单、item、受理时刻和主文件地址；事件检测每 60 秒取股票池里股票最近 24 小时的公告，全部表单都存。新闻工具的查询放在 `NewsLookupService`，补抓的界线是现在减去事件检测的回看时长。

2026-11-07 用 dev.env 的 SEC User-Agent 和 Alpaca key 测了 32 只股票（含 7 只 ADR），下面的决定引用这次的数据。

## Goals / Non-Goals

**Goals:**

- Fundamentals 用工具读任务股票快照时刻以前的公告和电话会文字稿，模型改不了股票和时刻
- 任何排版的公告都能读到要找的那一段
- 补抓不影响事件检测

**Non-Goals:**

- 见 proposal 的 Non-goals

## Decisions

### D1：模块边界

涉及 `contracts`、`modules/news`、`integrations/sec`、`integrations/llm`、`bootstrap`。

| 位置 | 内容 |
| --- | --- |
| `contracts/news.py` | `FilingDocument`：文件类型、文件名、纯文本 |
| `modules/news/ports.py` | `FilingSource.documents(filing)`；`NewsStore` 加 `filing(accession)`、`documents(accession)`、`add_documents(accession, documents)` |
| `modules/news/config.py` | `FilingConfig`：七个配置项 |
| `modules/news/domain/filings.py` | 能读的表单和原表单、`list_filings` 的范围和筛选、`FilingSearch`、按位置切文字、查找 |
| `modules/news/domain/transcripts.py` | 文字稿的范围、哪条新闻算文字稿、取最新一篇 |
| `modules/news/service.py` | `NewsService.fetch_filings` 加可选的结束时刻和表单类型，加 `filing`、`documents`；新增 `FilingLookupService`：`search`、`read`、`find`、`transcript` |
| `modules/news/repo.py` | 公告文件表和它的读写 |
| `integrations/sec/edgar.py` | `SecFilingSource.documents`：读目录页、下载文件、转成纯文本 |
| `integrations/llm/filings.py` | `filing_tools(lookup)` 给出 `ToolBuilder`；结果的格式和说明的文字 |
| `bootstrap` | 迁移 v0017；`Settings.filings`；Fundamentals 接上工具；股票在不在股票池用股票池模块的 `entry` |

模块之间传 `SubAgentRequest`、`Filing`、`NewsArticle`（已有）和 `FilingDocument`、`FilingSearch`、`FilingPage`、`FilingMatches`、`TranscriptSearch`（新增，后三个是 frozen dataclass，放在 `domain`）。工具结果只在 `integrations/llm/filings.py` 里序列化给模型，不进 `contracts`。

### D2：按位置读和查找，不按章节

Proposal 写的是 10-Q、10-K 按章节读。测试里按 Item 标题找管理层讨论：AAPL、NVDA、AMZN、GOOGL 等能找到；MSFT 只找到 3,702 字的一段，因为每页都重复 "PART I" 页眉；JPM、C、MS 的 10-Q 里一个 Item 标题都没有；NVO 的 20-F 每页重复 "ITEM 4 INFORMATION ON THE COMPANY"；ASML 的 20-F 135 万字里只认出 3 个标题。只列出全部 Item 标题也一样被页眉淹没。

所以 `read_filing` 不切章节：给起点就从那里读，最多 20,000 字；给查找就列出这段文字出现的位置。模型先查 "Management's Discussion"、"Outlook"、"Risk Factors" 这类短语，再按位置读。规则只有一条，任何排版都一样；代价是多一次工具调用。2026-11-07 已经确认，完整版 Proposal 在 apply 时改。

### D3：只列、只补抓能读的表单

一年的公告 JPM 26,462 份、GS 16,554 份、MS 19,220 份、C 13,191 份，绝大多数是 424B2 和 FWP；普通公司一年 60 到 330 份，大半是 4 和 144。Fundamentals 不分析内部人交易、持股和招股书，列出来只会挤掉财报。能读的五种表单普通公司一年约 16 份，TSM、NVO 这类发很多 6-K 的约 55 到 76 份。

补抓时 `NewsService.fetch_filings` 也只存这几种：不然 JPM 每次补抓都要写 2.6 万行。事件检测不传表单类型，照旧全部存。

### D4：股票在不在股票池

Proposal 写明股票池和持仓里的股票，公告只补 24 小时以前的。持仓一定在股票池里，事件检测监控的就是股票池的全部股票，所以只要问股票池。寻股会派 Fundamentals，候选常常就是刚出财报的股票，最近 24 小时的 8-K 不能看不到。

`FilingLookupService` 收一个 `Callable[[str], bool]`，bootstrap 传 `lambda symbol: pool.entry(symbol) is not None`，每次补抓时问一次，用的是现在的股票池，跟事件检测一致。不在股票池的股票补到时刻 T：它以后加入股票池时，早于加入时刻的公告事件检测本来就不判断（event-detection spec），存下也不会漏事件。

### D5：每次列表都问 SEC，不做覆盖表

submissions 的 `filings.recent` 至少有最近一年：32 只里最少的 XOM 有 41 行（换了 CIK），银行一年的全在里面。所以一次请求就覆盖 `filing_tool_days` 的默认 365 天，不用翻 `filings.files` 的旧页。写入按 accession number 幂等。JPM 的 submissions 有 2.6 万行，下载和解析的时间在主机冒烟时看一次。

### D6：文件转成纯文本存下

转换放在 SEC 适配器：`ix:header` 是 SEC 文件特有的，`html.parser` 不在纯区域的白名单里。存纯文本而不是 HTML：起点和查找的位置都是纯文本的字符位置，存下的文字不变，位置就不会因为以后改了转换规则而错开。同一份公告的文件一起取、一起存；表的主键是 accession number 加序号，写入用 `ON CONFLICT DO NOTHING`，同时取的两个调用各写各的，都读回全部文件。

目录页用 `<accession>-index.htm` 的文件表：2026-11-06 测过 `index.json` 没有文件类型，按文件名猜漏了 NVDA 的 `q2fy27pr.htm`，文件表 22 份全部认出 EX-99。只读 EX-99 和 EX-13：业绩稿和 CFO 说明在 EX-99（NVDA、JPM、WMT 都有 EX-99.2），WFC 的 10-K 正文只有 8.9 万字，管理层讨论在 EX-13 的年报里。6-K 有的正文就在主文件里（TSM、NVO、ARM），有的在 EX-99.1（SAP、ASML、BABA），所以两种都读。20-F 的 EX-13.x 是认证书，很短，一起读不值得为它加例外。

### D7：查找的规则

查找用 `re.finditer(re.escape(查找), 文字, re.IGNORECASE)`，查找前把文字和查找里的 ’ 都换成 '。一个字符换一个字符，`IGNORECASE` 也不改变长度，所以位置就是存下的文字里的位置；`casefold` 会把 ß 变成 ss，位置会错开，不用。所在的那一行取位置前后最近的换行。

### D8：文字稿从新闻里找

Finnhub 的文字稿接口免费 key 返回 403。Alpaca 新闻里 Benzinga 发全文：16 只股票 200 天里找到 20 篇，标题有 "Apple Q4 2026 Earnings Call Transcript"、"Transcript: NVIDIA Q1 2027 Earnings Conference Call"、"Full Transcript: Apple Q2 2026 Earnings Call"、"Coca-Cola Q1 2026 Earnings Call: Complete Transcript"、"JPMorgan Chase Reports Q2 2026 Results: Full Earnings Call Transcript" 五种写法，都含 transcript 和 earnings call 或 earnings conference call；正文 28,000 到 70,000 字符，只标注这一只股票。NVDA 的 "NVIDIA Launches Nemotron 3.5 Transcribe" 不含 earnings call；2026-11-06 见过 1,454 字符的 "(CORRECTED)" 短稿，用正文长度排除。

不做文字稿列表：要列出一年的文字稿就要补抓一年的新闻，NVDA 200 天就有 3,638 条。按截止日往前找最新一篇，一次最多补抓 100 天，和新闻工具的量级一样；要上一季度时，模型把截止日写到这一篇发布的前一天。有的季度没有稿（AAPL 的 Q3、MSFT 只有一篇），TSLA 的 Q2 晚了两个月才发，这些只能如实给出找到的那篇和它的发布时刻。

正文长度按存下的 HTML 算，`domain` 不用解析 HTML；给模型的正文按 news-tools spec 去掉 HTML，用 `integrations/llm/news.py` 的同一个函数。文字稿的补抓照搬新闻工具：界线对所有股票都一样，因为新闻只有股票池里的由事件检测写入。

### D9：条目 ID 和参数

公告的条目 ID 是 `filing/<accession number>`，不带 `s<版本>/`，和快照条目、`news/` 都不会相同；读文件、查找都引用这份公告的 ID，不细到文件。`read_filing` 的条目 ID、文件和 `list_filings` 的表单类型都是字符串：写错只回说明，不让参数校验失败把整次运行变成输出不合法（同 add-news-tools 的 design D7）。查不到的五种情况写法相同，也不透露快照以后有没有这份公告。

### D10：美东日期在 service 里换算

和新闻工具一样，`FilingLookupService` 把美东日期换成时刻，再交给 `domain`。

### 省略的情况

- XOM 2026-07 换了 CIK，旧 CIK 下的 10-K、10-Q 列不出来
- 40-F、PDF 和图片附件不读
- 目录页的文件表格式变了：原样报错
- 文件存下以后 SEC 改了：不更新
- 不在股票池的股票最近 24 小时发布的文字稿看不到：新闻只有事件检测写入最近 24 小时
- 模型超时以后还在跑的工具线程继续写入：写入幂等，不取消
- 工具调用不写程序日志：内容已经在模型调用留痕里

## Risks / Trade-offs

- 每次 `list_filings` 都下载一次 submissions，银行的几 MB → 主机冒烟时看 JPM 的耗时
- 工具次数默认 5：列表、查找、读、文字稿已经 4 次，后面还有 `financials`、`valuation` → 上线以后看实际次数再调 `max_sub_agent_tool_calls`
- 大文件里常见短语出现很多次：JPM 的 10-K 主文件 117 万字，管理层讨论是每页的页眉 → 一次最多给 20 处，可以从最后一处之后接着查，也可以换更具体的短语
- 读大文件首次下载慢：主文件几 MB，SEC 的超时是每次读写之间 10 秒，不是总时长 → 冒烟时看一次 10-K
- 文字稿占输入：一篇 3 万到 7 万字符 → 费用不是约束
