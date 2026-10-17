# Proposal

## Why

M4 后面的「事件检测」要按新闻和公告开案件，「状态库快照」要把一只股票最近的新闻和公告放进案件的快照，M5 的 News 子 agent 要读新闻原文。现在系统里还没有新闻和公告的来源，也没有地方存它们。数据源已经定了，全部用免费方案：新闻用 Alpaca 的新闻 API（Benzinga 稿件，用模拟盘现有的 key），公告用 SEC EDGAR（不要 key）。

本变更的目的只有一个：**把池内股票和持仓的新闻、SEC 公告取下来，存进共享状态库（当前市场事实，写入者是数据层）。**

引用的 Proposal（完整版）章节：

- 「架构 · 触发」的数据源：SEC EDGAR 公告和财报原文、新闻
- 「触发 · 1. 事件驱动」：只监控股票池和持仓；新闻和公告是事件来源之一，按来源、关键词和强度初筛（初筛由「事件检测」做，本变更只保证存下的字段够用）
- 「共享状态库」：新闻原文属于当前市场事实，写入者是数据层；只追加不覆盖，每类字段只有一个写入者
- 「技术栈与部署」的数据源一行：现在写着"新闻和一致预期：待定"

已经定了的（人已同意）：

- 新闻：Alpaca News API（`data.alpaca.markets` 的 `/v1beta1/news`），用现有的 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY`；免费档每分钟 200 次
- 公告：SEC EDGAR。股票代码到 CIK 用 `company_tickers.json`，每家公司的公告列表用 submissions JSON。请求带写明联系方式的 User-Agent，从新的环境变量读、由人填写；每秒不超过 10 次
- 财报日历、一致预期和行业用 Finnhub（「财报日历与板块」），寻股的日线用 Alpaca 历史行情（「寻股粗筛」），行情和到价提醒用 moomoo
- 事件规则由「事件检测」实现：新闻只看标注不超过 2 只股票、标题命中重大事件关键词的；公告看 8-K、10-Q、10-K、增发（S-3、424B）、13D，8-K 的 item 2.02 算财报发布，Form 4 不算。没开案件的条目也照样存下，PM 在下一个案件里能看到

## What Changes

- 新增模块 `modules/news/`（新闻和公告）：
  - 端口：新闻源（给定一组股票和起始时刻，取这段时间发布、标注了这些股票的新闻）、公告源（给定一组股票和起始时刻，取这些股票所属公司这段时间被 SEC 受理的公告）、存储；数据源超时、断线、限频或出错时报数据源不可用
  - 用例：取新闻、取公告：向数据源取，存下还没存过的（新闻按新闻 ID、公告按 accession number），返回这次新存下的条目；读一只股票某个时刻以来的新闻、公告
  - 存储：两张只追加的表 `news_articles`、`news_filings`，一个条目一行，写入以后不改不删；同时取到同一条时只存一次，只有一个调用把它当作新增
- 新增 contracts `contracts/news.py`：一条新闻（ID、标题、摘要、正文、作者、稿件来源、链接、标注的股票、发布时刻）、一份公告（accession number、标注的股票、表单类型、8-K 的 item、受理时刻、主文件地址）。「事件检测」「状态库快照」和 Agent 网关都读这两个格式
- 新增 Alpaca 新闻适配器 `integrations/alpaca/news.py`：取完全部分页，带正文；发布时刻早于起始时刻的条目不要；调用失败按 spec 分类
- 新增 SEC EDGAR 适配器 `integrations/sec/`：
  - 股票代码换成 SEC 的写法（BRK.B 是 BRK-B）后查 CIK；查不到时重新下载一次代码表，仍然查不到的股票没有公告
  - 同一家公司的几只股票只读一次 submissions；同一份公告出现在几家公司的列表里时合成一条
  - 受理时刻按实测的偏差换算成真实时刻（见 design D6）
  - 同一进程里对 SEC 的请求依次进行，相邻两次之间至少隔开配置的间隔（默认每秒最多 8 次）
  - 调用失败按 spec 分类
  - 配置：User-Agent（必须给出）、超时、每秒最多请求数；User-Agent 的环境变量是 `SEC_USER_AGENT`，代码和测试里不写具体邮箱
- 迁移 v0008 建两张表
- 新增 pytest 标记 `news_sources`：连真实 Alpaca 新闻和 SEC EDGAR 的冒烟测试，默认不运行；缺环境变量时跳过
- 完整版 Proposal 改两处（apply 时做）：
  - 「技术栈与部署」数据源一行改为：moomoo：行情、到价提醒和实盘交易；SEC EDGAR：公告和财报原文；Alpaca：新闻（Benzinga）和寻股用的日线；Finnhub：财报日历、一致预期和行业。都用免费档
  - 「架构 · 触发」数据源一句改为：moomoo 实时行情推送和到价提醒、SEC EDGAR 公告和财报原文、新闻、财报日历，见「技术栈与部署」

## Non-goals

- 装配：bootstrap、settings 不建这个模块和两个适配器，也不读 `SEC_USER_AGENT`；DBOS 不接。由有真正调用方的「事件检测」「状态库快照」「案件工作流」接上
- 定时拉取、Alpaca 新闻的 WebSocket 实时推送、拉取频率和起始时刻怎样往前推：「事件检测」
- 判断事件（关键词、标注的股票数、表单类型、8-K item）：「事件检测」
- 程序日志：Proposal 的行为者表里没有数据层；取到的新闻和公告由「事件检测」作为检测到的事件写
- 数据源取不到时的告警和健康检查：接线的变更决定
- 快照怎样读新闻和公告（读哪段时间、按写入时刻截止）：「状态库快照」
- 下载公告正文、XBRL 财报数值：M5 的查询工具和 Fundamentals
- 新闻正文转成纯文本：M5 给 agent 用时再定
- 寻股、财报日历、一致预期、行业：「寻股粗筛」「财报日历与板块」
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

- `news-and-filings`：新闻和公告模块：条目的格式、取新闻、取公告、读取、存储（只追加、按 ID 去重、同时写入、原样读回、读写出错）
- `alpaca-news`：Alpaca 新闻源：请求和分页、字段映射、起始时刻之前的条目、调用失败的分类、配置
- `sec-filings`：SEC EDGAR 公告源：代码到 CIK、submissions 的映射、受理时刻的换算、同一家公司和同一份公告、请求间隔、调用失败的分类、配置

### Modified Capabilities

- `state-store`：修改「表的归属和迁移」，加新闻表 `news_articles` 和公告表 `news_filings`

## Impact

- **代码**：
  - 新增 `src/tradingagentspro/modules/news/`（`__init__.py`、`ports.py`、`service.py`、`repo.py`）
  - 新增 `src/tradingagentspro/contracts/news.py`
  - 新增 `src/tradingagentspro/integrations/alpaca/news.py`，`integrations/alpaca/__init__.py` 导出它
  - 新增 `src/tradingagentspro/integrations/sec/`（`__init__.py`、`edgar.py`）
  - 迁移 `bootstrap/migrations/versions/v0008_create_news_tables.py`
  - `pyproject.toml`：pytest 标记 `news_sources`，`addopts` 默认排除它
- **测试**：`tests/modules/news/`（用例和数据库实现，后者要开着 OrbStack）、`tests/fakes/news.py`、`tests/integrations/alpaca/test_news.py`、`tests/integrations/sec/`（替身返回 2026-10-17 从真实 SEC 录下的数据）、两个冒烟测试；`tests/bootstrap/` 的表清单和最新迁移版本
- **依赖**：无（httpx 已有）
- **文档**：完整版 Proposal 两处（见 What Changes）；README「常用命令」加冒烟测试的运行方法
- **数据库**：两张新表、一个迁移；本变更以后还没有写入方
- **并行的变更**：「财报日历与板块」（v0009）也改 state-store「表的归属和迁移」和 `pytest` 的 `addopts`；后归档的那个在自己的 delta 里带上先归档的表
- **对后续变更的影响**：
  - 「事件检测」：
    - 起始时刻只在取成功以后往前推，取不到时下一轮从同一时刻再取；新闻源和公告源分开推
    - 13D 的表单类型有新旧两种写法：`SC 13D`、`SC 13D/A`，以及 2024-12 起的 `SCHEDULE 13D`、`SCHEDULE 13D/A`；增发要匹配 `S-3`、`S-3ASR`、`S-3/A` 和 `424B1` 到 `424B8`
    - SEC 每轮每家公司读一次 submissions（大公司压缩前 100 到 200 KB），轮询间隔按分钟级定
    - 装配时把 `SEC_USER_AGENT` 加进 settings 和部署的环境变量清单
  - 「状态库快照」：按股票和发布时刻（受理时刻）读；需要"快照时刻以前已经存下的"时，表里有写入时刻

## 需要人决定

1. **新闻存不存正文**：「共享状态库」写的是"新闻原文"。Alpaca 免费档加 `include_content` 能拿到 Benzinga 的正文（HTML），每条几 KB；池子 50 只股票时估计每天几百条、几 MB。
   推荐：存正文，原样存 HTML，给 agent 用时再转成文字。不存的话 M5 的 News 子 agent 只有标题和摘要。
2. **公告存哪些、存到什么程度**：submissions 里除了事件规则看的那几种，还有 Form 4、144、13G、委托书等（AAPL 一年几百份，大多是 Form 4）。
   推荐：全部表单类型都存，只存条目（表单类型、item、受理时刻、主文件地址），不下载正文；事件规则只看那几种，PM 在案件里也能看到内部人交易。正文由 M5 的查询工具按地址去取。
3. **保留多久**：
   推荐：一直保留，不清理。估计一年 1 到 2 GB，主要是新闻正文；以后占地方了再定。
4. **在 SEC 查不到 CIK 的股票**：比如 SEC 的代码表还没收录刚上市的股票。
   推荐：当作没有公告，不报错，其他股票照常取；查不到时先重新下载一次代码表，仍然查不到的在本进程里不再重新下载。另一种做法是整次报错，但那样一只股票会挡住全部股票的公告。
5. **取到一半出错**：比如 50 家公司读到第 30 家时 SEC 返回 503。
   推荐：整次报数据源不可用，什么都不存；下一轮从同一个起始时刻重取，已经取到的那 30 家晚一轮才存下。另一种做法是先存已经取到的，但要多一种"部分成功"的结果。
6. **新闻后来被改了**：Benzinga 偶尔会改标题或正文，新闻 ID 不变。
   推荐：只存第一次取到的版本，之后同一 ID 的不再写入，也不算新增。改动多是补充细节，算新增会让「事件检测」对同一条新闻再判断一次。
