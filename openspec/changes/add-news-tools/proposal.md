# Proposal

## Why

News 现在只看得到 PM 派任务时写进输入的几条新闻，要回答"发生了什么、影响多大、后续怎样"，得自己查这只股票的新闻。「子 agent 调用」已经留好读取工具的位置，这个变更接上 News 的两个工具。

## What Changes

- 新增读取工具 `list_news`：列出任务股票的新闻，最新的在前，可以按美东日期和关键词筛；不写日期时查最近 7 天，最多 50 条，超过时写明一共多少条
- 新增读取工具 `read_news`：读一条新闻的全文，去掉 HTML；没有正文的写明没有
- 股票和快照时刻由程序注入，模型改不了；只返回快照时刻以前发布的新闻
- 最早查到快照时刻以前 90 天，更早的裁掉并写明
- 范围里早于事件检测回看时长（默认 24 小时）的部分每次向新闻源补抓，存下再读；最近 24 小时只读状态库，由事件检测写入
- 新闻源不可用时照样返回库里已有的，并写明这段可能不全
- 列表返回过的和全文读过的新闻都算工具返回过的内容，可以引用
- 新闻源可以按结束时刻取；模块可以按新闻 ID 读一条
- News 换上这两个工具，Fundamentals 还是没有读取工具

引用完整版 Proposal 的章节：「Agent」的「News」。

**Non-goals：**

- 公告工具和 Fundamentals 的工具：在「公告工具」和后面的变更
- 覆盖表：见 design D3
- prompt 和宪章里怎样用这些工具：在「写 prompt」里写，文件保持占位
- 切换真实模型：线上继续用替身
- 工具调用写程序日志：完整的工具调用和返回已经在模型调用留痕里

## Capabilities

### New Capabilities

- `news-tools`：`list_news`、`read_news` 的参数、结果、范围、补抓、引用和配置

### Modified Capabilities

- `news-and-filings`：取新闻可以给结束时刻；可以按新闻 ID 读一条
- `alpaca-news`：请求带结束时刻，去掉结束时刻之后发布的新闻
- `sub-agent-calls`：News 有读取工具
- `bootstrap`：装配 News 的读取工具
- `settings-file`：配置文件多一节 `news`

## Impact

- 代码：`modules/news`（新增 `config.py`、`domain/`，服务多一个查询入口）、`integrations/alpaca/news.py`、新增 `integrations/llm/news.py`、`bootstrap/app.py`、`bootstrap/settings.py`
- 测试：`tests/fakes/news.py`、`tests/modules/news`、`tests/integrations`、`tests/bootstrap`
- 配置：`config/settings.toml` 多一节可选项，服务器不用改
- 部署：随下一次更新上服务器，没有迁移；生产环境还是替身，替身不调用工具
- 文档：完整版 Proposal「News」的工具规则改写补抓那一条并写上范围；两份路线表更新类型

## 需要人决定

无
