# Proposal

## Why

SEC submissions 的 acceptanceDateTime 现在已经是真实的 UTC 时刻：2026-11-04 对照 20 份公告的页面，2026 年 6 月到 11 月的差值全是 0，连 2026-07-30 的旧公告也一样。sec-filings spec 是按 2026-10-17 的实测写的，适配器还在减 4 到 5 小时，所以新存下的公告受理时刻比实际早 4 到 5 小时。

这会让事件判断出错：受理时刻早于股票加入时刻的公告会被丢弃，加入后 4 到 5 小时内受理的公告因此漏判；事件的发生时刻也不对。M5 的工具要按快照时刻截止，受理时刻不准，回放时就可能看到之后几小时的公告。

## What Changes

- 适配器按 acceptanceDateTime 本身的 UTC 时刻读取受理时刻，不再换算
- 删除 sec-filings spec 的「受理时刻的换算」，换成「受理时刻」
- 基准数据的 acceptanceDateTime 改成 2026-11-04 从 SEC 读到的值

## Capabilities

### New Capabilities

无

### Modified Capabilities

- `sec-filings`：「股票代码到 CIK」的基准数据、「submissions 的映射」的受理时刻一行改写；「受理时刻的换算」换成「受理时刻」

## Impact

- 代码：`integrations/sec/edgar.py`，公告的格式和端口不变
- 测试：`tests/integrations/sec/` 的基准数据和受理时刻的测试
- 数据：没有迁移，已经存下的公告不改，见 design D2
- 部署：归档并提交以后更新服务器，不用停机处理

## 引用的 Proposal 章节

- 完整版「技术栈与部署」的数据源一行：SEC EDGAR 提供公告和财报原文

## Non-goals

- 修正服务器上已经存下的公告和事件的时刻
- 启动时或运行时检测 SEC 的时间是否又变了：真实接口的冒烟测试会断言受理时刻，手动运行时能发现
- 公告正文、M5 的工具和按快照时刻截止：属于 M5

## 对后续变更的影响

- M5 的工具按受理时刻截止时，可以直接用公告里的受理时刻

## 需要人决定

无
