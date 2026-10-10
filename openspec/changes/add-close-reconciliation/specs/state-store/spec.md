# Spec Delta

## MODIFIED Requirements

### Requirement: 表的归属和迁移

状态库的表和交易记录表（见 trade-records spec）SHALL 由迁移建立和修改：

- 在空数据库上升级到最新版本时，建出全部表
- 已经是最新版本时再升级，不做任何修改

每张表 SHALL 只由一个模块写入，表名以这个模块的名字加下划线开头。

#### Scenario: 空数据库升级到最新版本

- **WHEN** 在空数据库上升级到最新版本
- **THEN** 有预留表 risk_reservations、订单表 execution_orders、止损保护表 execution_protections、交易记录表 execution_trades、收盘对账表 execution_close_reconciliations，以及记录迁移版本的表
- **AND** 五张表都能保存和读取

#### Scenario: 已经是最新版本时再升级

- **GIVEN** 已经升级到最新版本，P1 的订单有 2 个版本
- **WHEN** 再升级一次
- **THEN** 表没有变化，P1 的订单仍有 2 个版本

#### Scenario: 表名以写入它的模块开头

- **WHEN** 列出升级之后的全部表，不含记录迁移版本的表
- **THEN** 每张表的名字都以一个模块的名字加下划线开头：risk_reservations 属于风控模块，execution_orders、execution_protections、execution_trades 和 execution_close_reconciliations 属于执行模块

## ADDED Requirements

### Requirement: 读取一段时间里的版本

按一个时刻 T 读取一张表的历史时，SHALL 给出这段时间里用过的每一个版本：

- 每个键在 T 时的版本：写入时刻早于 T 的最新版本；T 以前还没有版本的键没有这一项
- 写入时刻不早于 T 的全部版本

结果按键排序，同一个键按版本号排序。T 以前就已经被更新的版本替换掉的旧版本 MUST NOT 给出。读写出错时照「读写出错时报错」处理。

目前只有止损保护表这样读，用来找当天出现过的无保护仓位（close-reconciliation spec「差异」）。

场景里的记录都是止损保护，按股票保存。

#### Scenario: 按时刻读历史

- **GIVEN** BBB 有 3 个版本，写入时刻是 2026-10-13 15:00Z、2026-10-13 19:00Z、2026-10-14 15:00Z；CCC 有 1 个版本，写入时刻 2026-10-14 16:00Z；DDD 有 1 个版本，写入时刻 2026-10-12 15:00Z
- **WHEN** 按 2026-10-13 20:10Z 读取历史
- **THEN** 依次读到 BBB 的版本 2、版本 3，CCC 的版本 1，DDD 的版本 1
- **AND** 没有 BBB 的版本 1

#### Scenario: T 以后没有新版本

- **GIVEN** BBB 有 2 个版本，CCC 有 1 个版本，写入时刻都早于 2026-10-14 20:10Z
- **WHEN** 按 2026-10-14 20:10Z 读取历史
- **THEN** 读到 BBB 的版本 2 和 CCC 的版本 1，就是两只股票的最新版本

#### Scenario: 写入时刻正好是 T

- **GIVEN** BBB 有 2 个版本，写入时刻是 2026-10-14 15:00Z 和 2026-10-14 20:10Z
- **WHEN** 按 2026-10-14 20:10Z 读取历史
- **THEN** 读到 BBB 的版本 1 和版本 2

#### Scenario: 空表

- **GIVEN** 止损保护表是空的
- **WHEN** 按任何时刻读取历史
- **THEN** 结果为空
