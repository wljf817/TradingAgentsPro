# Proposal

## Why

Fundamentals 现在没有读取工具，状态库里的公告也只有表单、item 和受理时刻，没有正文，「财报对比预期」「指引变化」都拿不到原文。这个变更给 Fundamentals 接上读公告和电话会文字稿的三个工具。

## What Changes

- 新增读取工具 `list_filings`：列出任务股票能读的公告（8-K、6-K、10-Q、10-K、20-F 和它们的修正版），最新的在前，可以按表单类型和美东日期筛；不写日期时查到时刻 T 往前 365 天，最多 50 份
- 新增读取工具 `read_filing`：读一份公告里的一个文件（主文件、附件 99.x 和 13.x），从给出的位置起最多 20,000 字；也可以在文件里查找一段文字，给出每处的位置
- 新增读取工具 `read_transcript`：从新闻里找财报电话会文字稿，给出不晚于某一天发布的最新一篇的全文
- 股票和快照时刻由程序注入，模型改不了；只返回快照时刻以前受理的公告和发布的文字稿
- 列公告时向 SEC 补抓：股票池里的股票只补到事件检测的回看时长以前，别的股票补到最新；只存能读的表单
- 读公告时，文件第一次读到就转成纯文本存下，之后只读状态库
- 取公告可以给出结束时刻和表单类型；模块能按 accession number 读一份公告、读它的文件
- SEC 公告源能读一份公告的文件列表和文件内容
- Fundamentals 换上这三个工具；News 的工具不变

引用完整版 Proposal 的章节：「Agent」的「Fundamentals」。

**Non-goals：**

- `financials`、`valuation`：在「财务数字」「估值」两个变更
- 40-F 和其他表单（招股书、委托书、持股报告、内部人交易）的内容：不列也不读
- PDF 和图片附件：只读网页和纯文本文件
- prompt 和宪章里怎样用这些工具：在「写 prompt」里写，文件保持占位
- 切换真实模型：线上继续用替身
- 工具调用写程序日志：完整的调用和返回已经在模型调用留痕里

## Capabilities

### New Capabilities

- `filing-tools`：`list_filings`、`read_filing`、`read_transcript` 的参数、结果、范围、补抓、引用和配置

### Modified Capabilities

- `news-and-filings`：取公告可以给出结束时刻和表单类型；按 accession number 读一份公告；公告的文件取一次、存下、读回；存储多一张文件表
- `sec-filings`：读一份公告的文件列表和文件内容，转成纯文本
- `news-tools`：Fundamentals 没有 News 的两个工具，但有自己的读取工具
- `sub-agent-calls`：Fundamentals 有读取工具
- `bootstrap`：装配 Fundamentals 的读取工具
- `settings-file`：配置文件多一节 `filings`
- `state-store`：多一张表 news_filing_documents

## Impact

- 代码：`contracts/news.py`（公告的文件）、`modules/news`（配置、`domain/` 新增公告和文字稿的规则、`FilingLookupService`、存储和表）、`integrations/sec/edgar.py`、新增 `integrations/llm/filings.py`、`bootstrap/app.py`、`bootstrap/settings.py`
- 迁移：v0017 新建公告文件表
- 测试：`tests/fakes/news.py`、`tests/modules/news`、`tests/integrations`、`tests/bootstrap`
- 配置：`config/settings.toml` 多一节可选项，服务器不用改
- 部署：随下一次更新上服务器，有一个迁移；生产环境还是替身，替身不调用工具
- 文档：完整版 Proposal「Fundamentals」的工具表和工具规则改成本变更的做法；两份路线表更新类型

## 需要人决定

无。已经确认：10-Q、10-K 不按章节读，`read_filing` 按位置读并能查找（design D2）；`list_filings` 只列能读的五种表单和修正版（design D3）；20-F 和 10-K 一样能读。完整版「Fundamentals」apply 时改成一致。
