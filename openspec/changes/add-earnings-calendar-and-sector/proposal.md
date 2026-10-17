# Proposal

## Why

M4 后面的变更要用到两样现在没有的数据：

- 财报日历：事件检测按它判断「财报发布」，共享上下文的重要日程和催化剂引用的日程项也要它；Fundamentals 解读财报时要对照一致预期和最近几个季度的 surprise
- 板块：股票池的板块「加入时由代码从数据源写入」，交易方案和持仓的板块都取这里；现在 `Listing.sector` 由调用方传入，项目里还没有地方能查到一只股票属于哪个板块，板块用哪套分类也还没定

数据源已经定了，全部用免费方案：财报日历、一致预期和 surprise 用 Finnhub 免费档；板块用 Finnhub 的行业分类（finnhubIndustry）对应到 11 个 SPDR 板块 ETF，信号以后按相对这个 ETF 的超额收益评估。

本变更的目的只有一个：**取财报日历（含一致预期和最近几季的 surprise）存进共享状态库，并提供「一只股票属于哪个板块」的查询。**

引用的 Proposal（完整版）章节：

- 「触发 · 1. 事件驱动」的事件来源：财报发布按财报日历
- 「股票池与寻股」的字段表板块一行：加入时由代码从数据源写入，交易方案和持仓的板块都取这里；用哪套板块分类跟数据源一起定
- 「共享上下文」的 Shared State：市场状态里的重要日程
- 「共享状态库」：财报数值属于当前市场事实，由数据层写入；写入带时间戳和版本号，只追加不覆盖，每类字段只有一个写入者
- 「技术栈与部署」：数据源一行（一致预期待定，本变更用 Finnhub）；API key 存环境变量

## What Changes

- 新增模块 `modules/companydata/`（公司数据）：
  - 板块：11 个 SPDR 板块 ETF，以及 Finnhub 行业到板块 ETF 的代码表（domain）。板块的值就是 ETF 的代码，比如 XLK
  - 端口 `CompanyDataSource`：一只股票的行业、一段日期内的财报日历、最近几个季度的 EPS surprise；超时、连不上、限频、服务端出错时报数据源不可用
  - 用例 `CompanyDataService`：
    - 查一只股票的板块：数据源查不到这只股票、或者行业不在代码表里时没有板块（见「需要人决定」第 1 条）
    - 刷新一只股票的财报：取窗口内（默认往前 30 天、往后 90 天）的财报日历和最近几个季度的 surprise，两项都取到才存
    - 读一只股票的财报日历和 surprise
  - 存储：两张只追加的版本表，财报日历按「股票 + 财年季度」保存每一次发布，发布日、一致预期、实际值有变化时追加新版本；surprise 按股票保存
  - 配置：财报日历的窗口
- 新增 `integrations/finnhub/`，用 Finnhub 免费档的三个接口实现端口：`/stock/profile2`（行业）、`/calendar/earnings`（财报日历，带 EPS 和营收的预期与实际值、盘前盘后）、`/stock/earnings`（surprise）。调用失败在这里分类：超时、连不上、429、5xx 报数据源不可用；401、403 等配置问题原样报错
- 迁移 v0009 建两张表
- 一组连真实 Finnhub 的冒烟测试，加新的 pytest 标记，默认不运行，没有 key 时跳过；拿到 key 后用它核对代码表
- apply 时改完整版「股票池与寻股」字段表的板块一行，写明用哪套分类，改为：

  > 加入时由代码从数据源写入，交易方案和持仓的板块都取这里，PM 不填；按 Finnhub 的行业分类对应到 11 个 SPDR 板块 ETF（XLK、XLF、XLV、XLY、XLP、XLE、XLI、XLB、XLU、XLRE、XLC）之一，板块记为这个 ETF 的代码；对应不上的股票不加入股票池

  简版不改

## Non-goals

- 接进 bootstrap、settings 和 DBOS：不装配用例和适配器，不读 key 的环境变量；两张表随启动时的迁移建好，一直是空的。由有真正调用方的变更接上：「事件检测」「状态库快照」「案件工作流」「收盘心跳」
- 定时拉取、刷新哪些股票、多久刷新一次、怎样控制每分钟的请求数：同上
- 判断财报发布事件（包括 8-K item 2.02）：「事件检测」
- 接健康监控（数据源不可用算不算健康异常）：同上
- 组装 `Listing`：交易所、证券类型和日均成交额由「寻股粗筛」用日线和行情数据补齐，板块调用本变更的查询
- 一致预期的修正历史、目标价、评级变动：Finnhub 都要付费；「盈利预期上调」已经决定从寻股条件里去掉，由「寻股粗筛」改 Proposal
- 宏观日程（议息、CPI 等）：见「需要人决定」第 3 条
- 改完整版「技术栈与部署」的数据源一行：由「新闻和公告」一次改好
- 已经在股票池和持仓里的股票的板块：加入时写入、之后不变，不回填
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

- `company-data`：公司数据模块：板块 ETF 和行业代码表，查板块，刷新和读取财报日历与 surprise，财报的存储，配置
- `finnhub-company-data`：Finnhub 数据源：请求、行业、财报日历和 surprise 的映射、调用失败的分类、Finnhub 配置

### Modified Capabilities

- `state-store`：修改「表的归属和迁移」，加公司数据模块的两张表

## Impact

- **代码**：
  - 新增 `src/tradingagentspro/modules/companydata/`：`__init__.py`、`ports.py`、`service.py`、`repo.py`、`config.py`、`domain/sector.py`、`domain/earnings.py`
  - 新增 `src/tradingagentspro/integrations/finnhub/`：`__init__.py`、`source.py`
  - 迁移 `bootstrap/migrations/versions/v0009_create_company_data.py`
  - 别的模块、workflows、bootstrap 的装配、contracts、shared 不改
- **测试**：`tests/modules/companydata/`、`tests/fakes/company_data.py`、`tests/integrations/finnhub/`；`tests/bootstrap/test_database.py`、`test_app.py` 的表的清单和最新迁移版本；冒烟测试默认不运行，CI 也不运行
- **配置**：`pyproject.toml` 登记 pytest 标记 `finnhub_api`，默认排除
- **依赖**：无新增，用已有的 httpx
- **数据库**：两张新表、一个迁移，本变更以后还没有写入方
- **文档**：完整版「股票池与寻股」字段表的板块一行
- **对后续变更的影响**：
  - 「寻股粗筛」：组装 `Listing` 时调用 `CompanyDataService.sector()`；没有板块的股票跳过（「需要人决定」第 1 条）
  - 「事件检测」「状态库快照」「收盘心跳」：决定刷新哪些股票、多久一次；刷新一只股票发 2 个请求，查板块发 1 个，所有调用合计不超过 Finnhub 免费档的每分钟 60 次。读到的财报日历按发布日和盘前盘后判断发布时刻
  - 「启动与装配」类的接线：key 从环境变量 `FINNHUB_API_KEY` 读取，和其他 key 一样
  - M6 的信号评估：板块的值就是板块 ETF 的代码，直接取它的行情算超额收益

## 需要人决定

1. **行业对应不上 11 个板块时怎么办（影响风控的板块集中度）**

   有两种情况：Finnhub 查不到这只股票（profile 为空），或者给的行业不在代码表里（比如 N/A，或者 Finnhub 新出现的行业名）。板块同时用在风控的板块集中度和 M6 的相对板块 ETF 评估上。

   决定：没有板块，查询结果为空；行业不在代码表里时写一条 WARNING 日志，写明股票和行业，之后补进代码表。「寻股粗筛」跳过没有板块的股票，这样股票池、方案和持仓里的板块都是 11 个 ETF 之一。对应不上的多半是空壳、基金和冷门股，本来也不在交易范围里。完整版板块一行的最后一句写"对应不上的股票不加入股票池"。

   考虑过：归入一个「其他」板块。这些互不相关的股票会共用一份板块集中度额度，M6 评估时也没有 ETF 可比，只能退到 SPY。

2. **一个行业横跨两个板块时归哪个（小问题）**

   代码表按行业对应，Finnhub 的一个行业有时横跨两个 SPDR 板块。最明显的是 Retail：亚马逊、家得宝在 XLY，沃尔玛、好市多在 XLP。

   决定：每个行业只对应一个 ETF，按这个行业里多数股票所在的 ETF 定（Retail 对应 XLY），接受少数股票的板块不准，不加按股票的例外表。design D2 列出了已知横跨的行业和归属。

3. **「共享上下文」的重要日程没有写包括哪些（小问题）**

   重要日程能用上本变更的财报日历；宏观日程（议息、CPI、非农）在 Finnhub 要付费，也没有别的现成免费接口。

   决定：本变更只提供股票的财报日历；重要日程里放不放宏观日程、从哪里取，等 M5 做共享上下文时再定。完整版不改。

由 Claude 按推荐决定。
