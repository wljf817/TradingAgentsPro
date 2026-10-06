# Tasks

## 1. 订单和止损单记下开仓信息和成交

- [ ] 1.1 先写订单的测试 `tests/modules/execution/domain/test_order.py`（design D4）：
  - `new_order`：买单记下方案的板块、再校验结果里生效的紧急阈值，累计费用 0；卖单记下板块，没有紧急阈值
  - `apply`：按回报记下累计费用（order-lifecycle spec「部分成交后全部成交」的累计费用 2.15）
  - `opening_order`：最近创建的、累计成交大于 0 的买单；只有卖单或买单都没成交时为 None
  - `tests/modules/execution/test_service.py`：order-lifecycle spec 改过的场景（买入方案提交限价买单、减仓方案提交市价卖单、部分成交后全部成交）

  `tests/modules/execution/builders.py` 的 `decision` 加参数 `emergency_threshold`，默认 0.08；已有的 `Order(...)` 断言加上新字段。验证：新测试因为字段和函数不存在而失败
- [ ] 1.2 实现：`Order` 加 `sector`（必填，放在 `symbol` 后面）、`emergency_threshold`、`fees`；`BrokerOrder` 加 `fees`（默认 0）；`new_order` 和 `apply` 记下它们；新增 `opening_order`，`stop_price_of` 改用它。`tests/fakes/broker.py` 的 `update` 加参数 `fees`。`ports.py` 里 `Broker` 的说明写明费用是这笔订单累计的。验证：1.1 的测试和全部已有测试通过，`uv run mypy` 通过
- [ ] 1.3 先写止损单的测试（design D3）：
  - `tests/modules/execution/domain/test_protection.py`：`StopOrder.apply` 在挂着时记下累计成交、成交均价和费用；全部成交、撤销、拒绝时结束并记下未成交股数、累计成交、成交均价和费用；累计成交增加时 `filled_at` 记为这次的时刻，不变或变少时不改；结果未知时这些都不变；`Protection.new_stop` 记下创建时刻
  - `tests/modules/execution/test_protection_service.py`：stop-protection spec「对齐止损单」改过和新增的场景：止损单成交后不再挂新的（记下成交和发现成交的时刻）、止损单部分成交后记下成交，不改单（再对齐时发现成交的时刻不变）、清仓没卖完时重新挂止损单（BBB-STOP-2 的创建时刻）

  验证：确认这些测试失败，已有测试仍然通过
- [ ] 1.4 实现：`StopReport` 加 `filled`、`average_price`、`fees`（都有默认值）；`StopOrder` 加 `created_at`（必填）、`filled`、`average_price`、`fees`、`filled_at`；`apply` 按 design D3 更新；`new_stop` 加参数 `now`，`_place_stop` 传入。`tests/fakes/stops.py` 的 `update` 加参数 `filled`、`average_price`、`fees`；测试里直接构造的 `StopOrder` 加上创建时刻。`ports.py` 里 `StopBroker` 的说明写明累计成交包括改单之前的。验证：1.3 的测试和全部已有测试通过

## 2. 交易记录

- [ ] 2.1 先写测试 `tests/modules/execution/domain/test_trade.py`（design D1、D6），只测纯函数，订单和止损单直接构造：
  - `trade_of_order`：买单和卖单的类型、来源方案、委托股数、累计成交、成交均价、累计费用、状态、创建时刻；成交日是创建时所在时段的交易日，收盘后才更新的成交也一样；结算日是下一个交易日，周五到下周一、2026-11-25 跳过感恩节到 2026-11-27；累计成交为 0 时没有成交日和结算日
  - `trade_of_stop`：spec「止损单交易记录的状态」那张表的每一行；委托股数是未成交加累计成交；来源方案就是传入的方案 ID；成交日按 `filled_at`：盘中是当天，2026-10-15 13:00Z（开盘前）是 2026-10-14
  - `Trade.settled`：spec「结算状态随执行交易日变化」

  验证：确认因为模块不存在而失败
- [ ] 2.2 实现 `modules/execution/domain/trade.py`：`TradeKind`、`Trade`（含 `is_sale`、`settled`）、`trade_of_order`、`trade_of_stop`。验证：2.1 的测试通过；`uv run pytest tests/architecture`、`uv run lint-imports`、`uv run mypy` 通过

## 3. 账户数据的计算

- [ ] 3.1 先写测试 `tests/modules/execution/domain/test_account.py`（design D6、D7），输入直接构造：
  - `unsettled_proceeds`：当天的卖出在盘中未结算、收盘以后算已结算；部分成交按已成交的部分；止损单也算；前一交易日的卖出已结算；买入不算；周五的卖出在周六算已结算
  - `last_trade_days`：买入、卖出、止损单都算，取最晚的；没有成交的不算
  - `held_position`：板块、止损价、紧急阈值取最近创建、有成交的开仓订单；当天的开仓订单有成交时给出它的成交均价，不在盘中或不是当天的没有；没有有成交的开仓订单时为 None
  - `account_of`：组合以上几项，来历不明的持仓不列出

  验证：确认因为模块不存在而失败
- [ ] 3.2 实现 `modules/execution/domain/account.py`：`BrokerPosition`、`BrokerAccount`、`HeldPosition`、`Account`，以及 `unsettled_proceeds`、`last_trade_days`、`held_position`、`account_of`。执行交易日写成私有函数，和风控的定义相同。验证：3.1 的测试通过；架构测试、`uv run lint-imports`、`uv run mypy` 通过

## 4. 交易记录的存储

- [ ] 4.1 `ports.py` 新增 `TradeStore`，`tests/fakes/trades.py` 新增 `InMemoryTradeStore`（design D8）。先写测试：
  - `tests/modules/execution/test_repo.py`：同时跑内存实现和数据库实现：按键读到最新版本、没有这个键、表里没有记录时列出、spec「列出有成交的交易记录」，以及旧版本没有成交、最新版本有成交时会列出；只跑数据库实现：spec「交易记录原样读回」、重启后读到同样的交易记录
  - `tests/bootstrap/test_database.py`：state-store spec「表的归属和迁移」改过的两个场景，有四张表，`execution_trades` 属于执行模块

  验证：确认数据库实现和迁移的用例失败，内存实现的用例通过
- [ ] 4.2 实现：`repo.py` 的 `execution_trades` 表和 `PostgresTradeStore`；迁移 `bootstrap/migrations/versions/v0002_create_trade_records.py`（`down_revision` 是 `"0001"`）；包根导出 `TradeStore`、`PostgresTradeStore`、`Trade`、`TradeKind`。验证：4.1 的测试通过；`uv run lint-imports`、`uv run pytest tests/architecture`、`uv run mypy` 通过

## 5. 执行模块写交易记录

- [ ] 5.1 `ExecutionService` 的构造参数加 `trades: TradeStore`，`tests/modules/execution/builders.py` 的 `execution_service` 加参数，默认用新的 `InMemoryTradeStore`。先写测试 `tests/modules/execution/test_trade_service.py`，覆盖 trade-records spec 的「交易记录的内容」「交易记录的写入」「止损单交易记录的状态」「成交日和结算日」的全部场景。"存储会出错"用一个包装内存实现的替身：安排下一次 `save` 抛异常。验证：确认这些测试失败（还没有写交易记录）
- [ ] 5.2 实现 design D2：`_save_order`、`_save_protection`，替换 `service.py` 里全部的 `self._store.save` 和 `self._protections.save`。验证：5.1 的测试通过；执行模块已有的全部测试仍然通过

## 6. 账户数据

- [ ] 6.1 `Broker` 加 `account`（design D7）；`tests/fakes/broker.py` 增加可以设定的券商账户（权益、现金、前一交易日收盘时的权益、持仓和均价），`fail_next("account")` 可以安排断线。先写测试 `tests/modules/execution/test_account_service.py`，覆盖 trade-records spec 的「已结算资金」「最近成交日」「账户数据」全部场景，以及「保存交易记录」的「重启后给出同样的账户数据」。验证：确认这些测试失败
- [ ] 6.2 实现 `ExecutionService.account`，包根导出 `Account`、`HeldPosition`、`BrokerAccount`、`BrokerPosition`；包根的说明加上交易记录和 trade-records spec。验证：6.1 的测试通过；`uv run lint-imports`、`uv run mypy` 通过

## 7. 收尾

- [ ] 7.1 人同意 proposal「需要人决定」第 1、2 条以后，按推荐修改完整版 Proposal：「交易记录」最后一段，和「订单生命周期」每日对账一行。验证：只改了这几处，和 trade-records spec 一致
- [ ] 7.2 对照四个 spec 文件，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 7.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`，全部通过
