# Spec Delta

## MODIFIED Requirements

### Requirement: 表的归属和迁移

状态库的表和交易记录表（见 trade-records spec）SHALL 由迁移建立和修改：

- 在空数据库上升级到最新版本时，建出全部表
- 已经是最新版本时再升级，不做任何修改

每张表 SHALL 只由一个模块写入，表名以这个模块的名字加下划线开头。

#### Scenario: 空数据库升级到最新版本

- **WHEN** 在空数据库上升级到最新版本
- **THEN** 有预留表 risk_reservations、订单表 execution_orders、止损保护表 execution_protections、交易记录表 execution_trades，以及记录迁移版本的表
- **AND** 四张表都能保存和读取

#### Scenario: 已经是最新版本时再升级

- **GIVEN** 已经升级到最新版本，P1 的订单有 2 个版本
- **WHEN** 再升级一次
- **THEN** 表没有变化，P1 的订单仍有 2 个版本

#### Scenario: 表名以写入它的模块开头

- **WHEN** 列出升级之后的全部表，不含记录迁移版本的表
- **THEN** 每张表的名字都以一个模块的名字加下划线开头：risk_reservations 属于风控模块，execution_orders、execution_protections 和 execution_trades 属于执行模块
