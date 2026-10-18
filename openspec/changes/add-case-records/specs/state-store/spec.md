# Spec Delta

## MODIFIED Requirements

### Requirement: 表的归属和迁移

状态库的表、交易记录表（见 trade-records spec）、程序日志表（见 program-log spec）、Agent 网关的任务表、Result 表和费用表（见 task-dispatch spec「任务的存储」、call-budget spec「费用的存储」）、新闻表和公告表（见 news-and-filings spec「新闻和公告的存储」），以及案件记录的案件表、方案表和发现表（见 case-records spec「案件记录的存储」）SHALL 由迁移建立和修改：

- 在空数据库上升级到最新版本时，建出全部表
- 已经是最新版本时再升级，不做任何修改

每张表 SHALL 只由一个模块写入，表名以这个模块的名字加下划线开头。

#### Scenario: 空数据库升级到最新版本

- **WHEN** 在空数据库上升级到最新版本
- **THEN** 有预留表 risk_reservations、订单表 execution_orders、止损保护表 execution_protections、交易记录表 execution_trades、收盘对账表 execution_close_reconciliations、股票池表 stockpool_entries、跟进表 followups_follow_ups、程序日志表 programlog_entries、任务表 gateway_tasks、Result 表 gateway_results、费用表 gateway_costs、新闻表 news_articles、公告表 news_filings、财报日历表 companydata_earnings_releases、surprise 表 companydata_earnings_surprises、粗筛结果表 screener_runs、快照表 statestore_snapshots、案件表 caserecords_cases、方案表 caserecords_proposals、发现表 caserecords_findings，以及记录迁移版本的表
- **AND** 预留表到跟进表这七张表、财报日历表、surprise 表、粗筛结果表、案件表和方案表都能保存和读取，程序日志表、Agent 网关的三张表、新闻表、公告表、快照表和发现表都能追加和读取

#### Scenario: 已经是最新版本时再升级

- **GIVEN** 已经升级到最新版本，P1 的订单有 2 个版本，程序日志有 3 条
- **WHEN** 再升级一次
- **THEN** 表没有变化，P1 的订单仍有 2 个版本，程序日志仍是那 3 条

#### Scenario: 表名以写入它的模块开头

- **WHEN** 列出升级之后的全部表，不含记录迁移版本的表
- **THEN** 每张表的名字都以一个模块的名字加下划线开头：risk_reservations 属于风控模块，execution_orders、execution_protections、execution_trades 和 execution_close_reconciliations 属于执行模块，stockpool_entries 属于股票池模块，followups_follow_ups 属于跟进审批模块，programlog_entries 属于程序日志模块，gateway_tasks、gateway_results 和 gateway_costs 属于 Agent 网关模块，news_articles 和 news_filings 属于新闻和公告模块，companydata_earnings_releases 和 companydata_earnings_surprises 属于公司数据模块，screener_runs 属于寻股粗筛模块，statestore_snapshots 属于状态库模块，caserecords_cases、caserecords_proposals 和 caserecords_findings 属于案件记录模块
