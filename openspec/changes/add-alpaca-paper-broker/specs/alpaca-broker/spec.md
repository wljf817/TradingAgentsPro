# Spec Delta

## Purpose

Alpaca 适配器用 Alpaca Trading API 实现执行模块的两个券商端口（普通订单和止损单），让执行模块在 Alpaca 模拟盘上下单、跟进、挂止损单和对账，业务代码不区分模拟盘和实盘。

## ADDED Requirements

### Requirement: 订单状态的映射

适配器 SHALL 把 Alpaca 的订单状态映射为执行模块的 5 种券商回报：

| Alpaca 状态 | 券商回报 |
| --- | --- |
| new、accepted、pending_new、accepted_for_bidding、partially_filled、done_for_day、pending_cancel、pending_replace、stopped、suspended、calculated、held | 已受理、未结束 |
| filled | 全部成交 |
| canceled、expired | 撤销或失效 |
| rejected | 拒绝 |
| replaced | 不直接映射，按替换它的那一笔，见「改单产生的替换」 |
| 其他 | 结果未知 |

正在撤单（pending_cancel）和正在改单（pending_replace）仍算未结束，撤单或改单生效之前还可能成交。

本 spec 的场景除非另有说明，都使用下面的基准数据：

- 每笔佣金 2
- 订单 P1：买入 BBB 100 股，限价单，限价 50；客户端订单 ID 是 P1 的方案 ID，场景里简写为 P1
- 订单 P5：卖出 AAA 60 股，市价单
- 止损单 BBB-STOP-1：BBB 100 股，止损价 47
- "Alpaca 有订单 O1：……"指 Alpaca 那边保存着这样一笔订单，O1 是它的订单号。没写的已成交股数是 0，没有成交均价，没有被替换，也没有替换别的订单
- "查询 P1"指按 P1 向适配器查询这笔订单；"查询 BBB-STOP-1"指按止损单 BBB-STOP-1 向适配器查询

#### Scenario: 未结束的状态

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态依次取 new、accepted、pending_new、partially_filled、pending_cancel、done_for_day
- **WHEN** 每种状态下各查询一次 P1
- **THEN** 每次的券商回报都是已受理、未结束，订单号 O1

#### Scenario: 全部成交

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态 filled，已成交 100 股，成交均价 49.95
- **WHEN** 查询 P1
- **THEN** 券商回报全部成交，累计成交 100，成交均价 49.95

#### Scenario: 撤销和失效

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，已成交 30 股，成交均价 49.90，状态先是 canceled，再改为 expired
- **WHEN** 每种状态下各查询一次 P1
- **THEN** 两次的券商回报都是撤销或失效，累计成交 30，成交均价 49.90

#### Scenario: 拒绝

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态 rejected
- **WHEN** 查询 P1
- **THEN** 券商回报拒绝，累计成交 0

#### Scenario: 认不出的状态

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态是表里没有的 some_new_status
- **WHEN** 查询 P1
- **THEN** 券商回报结果未知，订单号 O1

### Requirement: 查询订单

按客户端订单 ID 查询一笔普通订单时，适配器 SHALL 向 Alpaca 按客户端订单 ID 查询，查不到时返回查不到；查到时返回的券商回报如下，被替换过的按「改单产生的替换」处理：

| 项 | 取值 |
| --- | --- |
| 券商订单号 | Alpaca 的订单号 |
| 状态 | 按「订单状态的映射」 |
| 累计成交股数 | 已成交股数 |
| 成交均价 | Alpaca 的成交均价；没有成交时没有 |
| 累计费用 | 按「费用估算」 |

Alpaca 用字符串给出的股数和价格 SHALL 按十进制精确读取，不经过浮点数。查询范围不受订单创建日期的限制，所以覆盖订单创建的那个交易日。

#### Scenario: 已提交，没有成交

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态 new
- **WHEN** 查询 P1
- **THEN** 券商回报：订单号 O1，已受理、未结束，累计成交 0，没有成交均价，累计费用 0

#### Scenario: 部分成交

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态 partially_filled，已成交 30 股，成交均价 49.955
- **WHEN** 查询 P1
- **THEN** 券商回报：已受理、未结束，累计成交 30，成交均价正好是 49.955，累计费用 2

#### Scenario: 查不到

- **GIVEN** Alpaca 没有客户端订单 ID 为 P1 的订单
- **WHEN** 查询 P1
- **THEN** 返回查不到

### Requirement: 提交当日有效单

提交普通订单时，适配器 SHALL 向 Alpaca 提交一笔订单，内容如下，并返回 Alpaca 受理后的券商回报（同「查询订单」）：

| 项 | 取值 |
| --- | --- |
| 客户端订单 ID | 订单的客户端订单 ID，也就是方案 ID |
| 股票、股数 | 订单的股票和委托股数 |
| 方向 | 买单 buy，卖单 sell |
| 类型 | 限价单 limit，带上限价；市价单 market |
| 有效期 | day（当日有效） |

Alpaca 拒绝受理时（见「调用失败的分类」），SHALL 报拒绝受理，这时没有生成订单。

#### Scenario: 提交限价买单

- **GIVEN** Alpaca 没有客户端订单 ID 为 P1 的订单
- **WHEN** 提交 P1，Alpaca 受理为订单 O1，状态 pending_new
- **THEN** Alpaca 收到的订单：客户端订单 ID P1，BBB，100 股，buy，limit，限价 50，day
- **AND** 券商回报：订单号 O1，已受理、未结束，累计成交 0

#### Scenario: 提交市价卖单

- **WHEN** 提交 P5，Alpaca 受理为订单 O5，状态 accepted
- **THEN** Alpaca 收到的订单：客户端订单 ID 是 P5 的方案 ID，AAA，60 股，sell，market，day，没有限价
- **AND** 券商回报：订单号 O5，已受理、未结束

#### Scenario: 购买力不足被拒

- **WHEN** 提交 P1，Alpaca 返回 403，说明购买力不足
- **THEN** 报拒绝受理，带上 Alpaca 的说明

### Requirement: 撤单

撤普通订单或止损单时，适配器 SHALL 按 Alpaca 的订单号请求撤单，撤单请求被接受就返回，撤单的结果由之后的查询得知。订单已经结束、撤不了（Alpaca 返回 422），或者 Alpaca 查不到这个订单号（返回 404）时，SHALL 同样正常返回。

#### Scenario: 撤单请求被接受

- **GIVEN** 订单 P1 的券商订单号是 O1，Alpaca 那边 O1 挂着
- **WHEN** 撤 P1
- **THEN** Alpaca 收到撤 O1 的请求，正常返回

#### Scenario: 订单已经结束

- **GIVEN** 订单 P1 的券商订单号是 O1，Alpaca 那边 O1 已经全部成交，撤单返回 422
- **WHEN** 撤 P1
- **THEN** 正常返回

#### Scenario: Alpaca 查不到订单号

- **GIVEN** 订单 P1 的券商订单号是 O1，Alpaca 撤 O1 返回 404
- **WHEN** 撤 P1
- **THEN** 正常返回

#### Scenario: 撤止损单

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2
- **WHEN** 撤 BBB-STOP-1
- **THEN** Alpaca 收到撤 O2 的请求，正常返回

### Requirement: 持仓

查询一只股票的持仓时，适配器 SHALL 返回 Alpaca 那边这只股票的持仓股数，没有持仓（Alpaca 返回 404）时为 0。查询全部持仓时，SHALL 返回股数大于 0 的每只股票和它的股数。

#### Scenario: 查询一只股票的持仓

- **GIVEN** Alpaca 持有 BBB 100 股
- **WHEN** 查询 BBB 的持仓
- **THEN** 返回 100

#### Scenario: 没有持仓

- **GIVEN** Alpaca 没有 CCC 的持仓
- **WHEN** 查询 CCC 的持仓
- **THEN** 返回 0

#### Scenario: 全部持仓

- **GIVEN** Alpaca 持有 AAA 60 股、BBB 100 股
- **WHEN** 查询全部持仓
- **THEN** 返回 AAA 60、BBB 100

### Requirement: 账户

读取账户时，适配器 SHALL 返回：

| 项 | 取值 |
| --- | --- |
| 权益 | Alpaca 账户的 equity |
| 现金 | Alpaca 账户的 cash，已经包含还没结算的卖出款 |
| 前一交易日收盘时的权益 | Alpaca 账户的 last_equity |
| 持仓 | 股数大于 0 的每只持仓：股票、股数、均价（avg_entry_price） |

#### Scenario: 读取账户

- **GIVEN** Alpaca 账户：equity 100000，cash 40000，last_equity 101000；持有 AAA 60 股，均价 98.5；BBB 100 股，均价 49.95
- **WHEN** 读取账户
- **THEN** 返回权益 100000，现金 40000，前一交易日收盘时的权益 101000，持仓 AAA 60 股均价 98.5、BBB 100 股均价 49.95

#### Scenario: 没有持仓

- **GIVEN** Alpaca 账户：equity 100000，cash 100000，last_equity 100000，没有持仓
- **WHEN** 读取账户
- **THEN** 返回的持仓为空

### Requirement: 全部挂单

读取全部挂单时，适配器 SHALL 读取 Alpaca 全部未结束的订单，包括部分成交、正在撤单和正在改单的，按类型分开：

- 全部挂单（普通订单）：类型不是 stop 的订单
- 全部挂着的止损单：类型是 stop 的订单。正在改单时，被替换的旧单和替换它的新单可能同时未结束，这时只算替换它的新单

每一笔给出股票、客户端订单 ID 和 Alpaca 的订单号。

#### Scenario: 普通订单和止损单分开

- **GIVEN** Alpaca 未结束的订单：O1（P1，BBB，limit buy，partially_filled）、O2（BBB-STOP-1，BBB，stop sell，new）、O9（X1，CCC，limit sell，pending_cancel）
- **WHEN** 读取全部挂单和全部挂着的止损单
- **THEN** 全部挂单是 O1、O9
- **AND** 全部挂着的止损单是 O2

#### Scenario: 改单还在进行中

- **GIVEN** Alpaca 未结束的订单：O3（AAA-STOP-1，AAA，stop sell，pending_replace），O4（AAA，stop sell，pending_new，替换 O3）
- **WHEN** 读取全部挂着的止损单
- **THEN** 只有 O4 一张

### Requirement: 挂止损单

挂止损单时，适配器 SHALL 向 Alpaca 提交一笔订单，内容如下，并返回 Alpaca 受理后的止损单回报（同「查询止损单」）：

| 项 | 取值 |
| --- | --- |
| 客户端订单 ID | 止损单的客户端订单 ID |
| 股票、股数 | 止损单的股票和股数 |
| 方向、类型 | sell，stop（触发后按市价卖出） |
| 止损价 | 止损单的止损价 |
| 有效期 | gtc（撤单前一直有效，Alpaca 90 天后自动撤销），只在常规交易时段触发 |

Alpaca 拒绝受理时，SHALL 报拒绝受理。

#### Scenario: 挂单

- **WHEN** 挂 BBB-STOP-1，Alpaca 受理为订单 O2，状态 new
- **THEN** Alpaca 收到的订单：客户端订单 ID BBB-STOP-1，BBB，100 股，sell，stop，止损价 47，gtc
- **AND** 止损单回报：订单号 O2，已受理、未结束，未成交 100 股，止损价 47，累计成交 0

#### Scenario: 挂单被拒

- **WHEN** 挂 BBB-STOP-1，Alpaca 返回 403，说明可卖股数不足
- **THEN** 报拒绝受理

### Requirement: 查询止损单

查询止损单时，适配器 SHALL 在止损单受理过（有券商订单号）时按订单号查询，没受理过时按客户端订单 ID 查询；查不到时返回查不到。查到时按「改单产生的替换」找到现在的那一笔，返回：

| 项 | 取值 |
| --- | --- |
| 券商订单号、止损价 | 现在这一笔的 |
| 状态 | 现在这一笔的状态，按「订单状态的映射」 |
| 未成交股数 | 现在这一笔的委托股数减去它的已成交股数 |
| 累计成交股数、成交均价、累计费用 | 按「改单产生的替换」，包括替换之前的成交 |

#### Scenario: 按订单号查询

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2；Alpaca 有订单 O2：stop sell BBB 100 股，止损价 47，状态 new
- **WHEN** 查询 BBB-STOP-1
- **THEN** 止损单回报：订单号 O2，已受理、未结束，未成交 100 股，止损价 47，累计成交 0，累计费用 0

#### Scenario: 没受理过，按客户端订单 ID 查询

- **GIVEN** 止损单 BBB-STOP-1 没有券商订单号（挂单时超时）；Alpaca 有订单 O2：客户端订单 ID BBB-STOP-1，stop sell BBB 100 股，止损价 47，状态 accepted
- **WHEN** 查询 BBB-STOP-1
- **THEN** 止损单回报：订单号 O2，已受理、未结束，未成交 100 股

#### Scenario: 止损单全部成交

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2；Alpaca 有订单 O2：100 股，止损价 47，状态 filled，已成交 100 股，成交均价 46.80
- **WHEN** 查询 BBB-STOP-1
- **THEN** 止损单回报：全部成交，未成交 0，累计成交 100，成交均价 46.80，累计费用 2

#### Scenario: 查不到

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2，Alpaca 查这个订单号返回 404
- **WHEN** 查询 BBB-STOP-1
- **THEN** 返回查不到

### Requirement: 改单产生的替换

Alpaca 改单时生成一笔新订单替换旧单：旧单状态变为 replaced 并指向替换它的新单，新单指回它替换的旧单。查询普通订单或止损单查到的订单，适配器 SHALL 沿着"替换它的订单"一直找到没有被替换的那一笔，作为现在的这一笔；查询的时候改单还没完成（旧单正在改单、还没指向新单）时，现在的这一笔就是旧单。

累计成交股数 SHALL 是整条替换链上每一笔的已成交股数之和：从查到的这一笔沿"它替换的订单"往前到最初的那一笔，再往后到现在的这一笔。成交均价按每一笔的已成交股数加权平均，没有成交时没有。累计费用按整条链的累计成交算一次（见「费用估算」）。

#### Scenario: 改过一次单，按旧订单号查到新单

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2；Alpaca 有订单 O2：100 股，止损价 47，状态 replaced，被 O3 替换；O3：80 股，止损价 47，状态 new，替换 O2
- **WHEN** 查询 BBB-STOP-1
- **THEN** 止损单回报：订单号 O3，已受理、未结束，未成交 80 股，止损价 47，累计成交 0

#### Scenario: 替换之前的成交计入累计成交

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O3；Alpaca 有订单 O2：状态 replaced，已成交 10 股，成交均价 47，被 O3 替换；O3：委托 90 股，状态 partially_filled，已成交 30 股，成交均价 46.50，替换 O2
- **WHEN** 查询 BBB-STOP-1
- **THEN** 止损单回报：订单号 O3，已受理、未结束，未成交 60 股，累计成交 40，成交均价 46.625，累计费用 2

#### Scenario: 改了两次单

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2；Alpaca 有订单 O2：被 O3 替换；O3：被 O4 替换；O4：60 股，止损价 46，状态 new
- **WHEN** 查询 BBB-STOP-1
- **THEN** 止损单回报：订单号 O4，未成交 60 股，止损价 46

#### Scenario: 改单还在进行中

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2；Alpaca 有订单 O2：100 股，状态 pending_replace，还没有指向替换它的订单
- **WHEN** 查询 BBB-STOP-1
- **THEN** 止损单回报：订单号 O2，已受理、未结束，未成交 100 股

### Requirement: 改单

改止损单时，适配器 SHALL 按止损单现在的券商订单号向 Alpaca 请求改单，改为给出的股数和止损价；请求被接受就返回，改单的结果由之后的查询得知（见「改单产生的替换」）。Alpaca 拒绝改单时（比如订单正在改单或撤单、还没路由出去），SHALL 报拒绝受理，这时止损单保持原样。

#### Scenario: 改单请求被接受

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2，Alpaca 那边 O2 挂着
- **WHEN** 把 BBB-STOP-1 改为 80 股、止损价 47，Alpaca 生成替换订单 O3
- **THEN** Alpaca 收到对 O2 的改单：80 股，止损价 47
- **AND** 正常返回

#### Scenario: 改单被拒

- **GIVEN** 止损单 BBB-STOP-1 的券商订单号是 O2，Alpaca 那边 O2 正在改单
- **WHEN** 把 BBB-STOP-1 改为 80 股、止损价 47，Alpaca 返回 422
- **THEN** 报拒绝受理

### Requirement: 费用估算

Alpaca 模拟盘不收费，适配器 SHALL 按配置估算费用：一笔订单累计成交大于 0 时，累计费用是每笔佣金，否则是 0。止损单连同改单产生的替换算一笔。每笔佣金和 risk-rules spec 的每笔佣金是同一个配置值（默认 2），由装配时传入。

#### Scenario: 没有成交不收费

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态 canceled，已成交 0
- **WHEN** 查询 P1
- **THEN** 累计费用 0

#### Scenario: 有成交收一笔

- **GIVEN** Alpaca 有订单 O1：客户端订单 ID P1，状态 filled，已成交 100 股
- **WHEN** 查询 P1
- **THEN** 累计费用 2

#### Scenario: 每笔佣金为 0

- **GIVEN** 每笔佣金是 0；Alpaca 有订单 O1：客户端订单 ID P1，状态 filled，已成交 100 股
- **WHEN** 查询 P1
- **THEN** 累计费用 0

### Requirement: 调用失败的分类

适配器 SHALL 按下表把调用 Alpaca 的失败分成三类：

| 情况 | 结果 |
| --- | --- |
| 超时、连不上、连接中断；Alpaca 返回 429（限频）或 5xx | 券商不可用：不知道这次调用有没有生效 |
| 提交订单、挂止损单、改单时，Alpaca 返回 401 以外的其他 4xx（比如 400、403、422） | 拒绝受理：提交和挂单时没有生成订单，改单时订单保持原样 |
| 查询订单或持仓返回 404；撤单返回 404 或 422 | 按各操作的约定：查不到、持仓为 0、正常返回 |
| 其他（401，以及查询、撤单返回的其他 4xx） | 原样报错：是配置或程序的问题，不当作券商不可用 |

适配器 MUST NOT 自己重试，重试由执行模块按订单和止损单的状态决定。

#### Scenario: 查询超时

- **WHEN** 查询 P1，Alpaca 超过超时时长没有响应
- **THEN** 报券商不可用

#### Scenario: 连不上

- **WHEN** 读取全部挂单，连不上 Alpaca
- **THEN** 报券商不可用

#### Scenario: 限频

- **WHEN** 提交 P1，Alpaca 返回 429
- **THEN** 报券商不可用，不报拒绝受理

#### Scenario: 服务端错误

- **WHEN** 读取账户，Alpaca 返回 503
- **THEN** 报券商不可用

#### Scenario: 提交时参数不合规

- **WHEN** 提交 P1，Alpaca 返回 422
- **THEN** 报拒绝受理

#### Scenario: API key 错误

- **WHEN** 查询 P1，Alpaca 返回 401
- **THEN** 原样报错，既不是券商不可用，也不是拒绝受理

### Requirement: Alpaca 的防自成交保护

Alpaca 会拒绝可能和同一账户反方向挂单成交的新订单（返回 403），模拟盘也一样。和本系统有关的是：

- 同一只股票挂着止损卖单时，新的买单被拒
- 同一只股票挂着限价买单时，新的止损卖单被拒

适配器 SHALL 把这种拒绝当作拒绝受理，MUST NOT 为了绕开它撤掉或修改别的挂单。避开这种拒绝是执行模块的事：在 Alpaca 上，执行模块把同一只股票的买单和止损单错开（「买单和止损单错开」变更）。万一仍然碰到，按执行模块已有的约定处理：买单记为已拒绝（order-lifecycle spec「先查询再提交」）；止损单按 stop-protection spec「挂单或改单被拒」处理。

#### Scenario: 持仓挂着止损单时加仓被拒

- **GIVEN** Alpaca 挂着 BBB 的止损卖单 O2
- **WHEN** 提交买入 BBB 的限价单，Alpaca 返回 403，说明可能构成自成交
- **THEN** 报拒绝受理，O2 不变

#### Scenario: 买单还挂着时止损单被拒

- **GIVEN** Alpaca 挂着 P1 的买单 O1，部分成交 30 股
- **WHEN** 挂 BBB-STOP-1（30 股），Alpaca 返回 403，说明可能构成自成交
- **THEN** 报拒绝受理，O1 不变

### Requirement: 账户设置

适配器 SHALL 提供账户设置操作：把 Alpaca 账户设为不做空（no_shorting 为 true）、最大保证金倍数 1（max_margin_multiplier 为 "1"），从而按现金账户使用，见完整版「交易记录」。重复设置结果相同。设置失败时 SHALL 报错（分类同「调用失败的分类」里的查询）。

#### Scenario: 设置账户

- **WHEN** 设置账户
- **THEN** Alpaca 收到账户配置的修改：no_shorting 为 true，max_margin_multiplier 为 "1"

#### Scenario: 设置时连不上

- **WHEN** 设置账户，连不上 Alpaca
- **THEN** 报券商不可用

### Requirement: Alpaca 配置

适配器 SHALL 可以配置：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| 地址 | Alpaca Trading API 的地址 | 模拟盘 `https://paper-api.alpaca.markets` |
| 超时时长 | 每次请求等多久没有响应就算超时 | 10 秒 |
| API key ID、API secret | 每次请求都带上，用于认证 | 无，必须给出 |
| 每笔佣金 | 估算费用用，见「费用估算」 | 由装配时传入风控配置的值 |

超时时长 MUST 大于 0，否则配置报错。

#### Scenario: 默认值

- **WHEN** 只给出 API key ID 和 API secret
- **THEN** 请求发往 `https://paper-api.alpaca.markets`，超时时长 10 秒

#### Scenario: 请求带上认证

- **GIVEN** API key ID 是 K1，API secret 是 S1
- **WHEN** 查询 P1
- **THEN** Alpaca 收到的请求带有 API key ID K1 和 API secret S1

#### Scenario: 超时时长不合法

- **WHEN** 超时时长配置为 0
- **THEN** 配置报错
