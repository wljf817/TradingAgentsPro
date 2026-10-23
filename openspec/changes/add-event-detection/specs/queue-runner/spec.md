# Spec Delta

## ADDED Requirements

### Requirement: 排过的卖出成交

排队运行 SHALL 给出排过的卖出成交的客户端订单 ID：排队表里全部触发是卖出成交的案件（case-types spec「触发」）带的客户端订单 ID，不论还在排队、已经启动还是不再运行。事件检测用它让每笔卖出只排一次卖出复盘（event-detection spec「卖出成交」）。

- 只读排队表，不改队列
- 读出错（数据库连不上、表不存在）时 SHALL 报错，MUST NOT 当作没有排过

#### Scenario: 排过的卖出成交

- **GIVEN** 依次排进 CCC 的卖出成交（客户端订单 ID CCC-STOP-1）、BBB 的新闻事件、ZZZ 的卖出成交（客户端订单 ID P9）；K-1 按启动出队
- **WHEN** 读排过的卖出成交
- **THEN** 是 CCC-STOP-1 和 P9
- **WHEN** 重启，再读
- **THEN** 仍是 CCC-STOP-1 和 P9

#### Scenario: 没有排过

- **WHEN** 排队表是空的，读排过的卖出成交
- **THEN** 为空

#### Scenario: 读出错

- **GIVEN** 数据库连不上
- **WHEN** 读排过的卖出成交
- **THEN** 报错
