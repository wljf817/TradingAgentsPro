## MODIFIED Requirements

### Requirement: 启动对账

Alpaca 账户设置之后、Discord 和 DBOS 之前，程序 SHALL 让执行模块和券商对账一次（reconciliation spec「对账」），按结果处理：

| 结果 | 处理 |
| --- | --- |
| 没有不一致 | 写 INFO，继续启动 |
| 有不一致 | 写 WARNING，列出三类不一致（股票、客户端订单 ID、股数）；继续启动。执行模块按 reconciliation spec「有不一致时暂停开仓」暂停开仓。Discord 连上以后、DBOS 启动以前推送一条告警（discord-notification spec「告警」） |
| 读取券商失败 | 启动失败，见「启动失败」 |

告警写明：启动对账有不一致；每一条不一致一行，写法和 close-reconciliation spec「结果消息」里的差异行相同；已暂停开仓，之后的对账没有不一致时恢复。告警发不出去时只写错误日志，照常继续启动。

对账的结果 SHALL 保留下来，给以后的流程使用。

#### Scenario: 没有不一致

- **WHEN** 正常启动
- **THEN** 日志写出启动对账没有不一致，启动完成
- **AND** Discord 没有收到告警

#### Scenario: 来历不明的持仓

- **GIVEN** Alpaca 模拟盘持有 CCC 100 股，状态库里没有 CCC 的订单
- **WHEN** 正常启动
- **THEN** 启动完成
- **AND** 有一条 WARNING：启动对账有不一致，来历不明的持仓 CCC 100 股
- **AND** 保留的启动对账结果里有这项不一致
- **AND** Discord 连上以后、DBOS 启动以前收到一条告警，写明启动对账有不一致、来历不明的持仓 CCC 100 股、已暂停开仓

#### Scenario: 不认得的挂单

- **GIVEN** Alpaca 模拟盘挂着一笔买入 DDD 的订单，客户端订单 ID 是 X1，状态库里没有它
- **WHEN** 正常启动
- **THEN** 启动完成，有一条 WARNING 写明不认得的挂单 DDD、X1
- **AND** Discord 收到一条告警，写明不认得的挂单 DDD、X1
- **AND** 这笔挂单没有被撤销

#### Scenario: 告警发不出去

- **GIVEN** Alpaca 模拟盘持有 CCC 100 股，状态库里没有 CCC 的订单；Discord 连上以后，发告警时报错
- **WHEN** 正常启动
- **THEN** 有一条错误日志，含告警的文字和失败原因
- **AND** 启动完成
