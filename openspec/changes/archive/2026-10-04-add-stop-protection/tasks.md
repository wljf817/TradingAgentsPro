# Tasks

## 1. 订单记下止损价，接口和测试替身

- [x] 1.1 先写测试：
  - `tests/modules/execution/domain/test_order.py`：`new_order` 给买入、加仓订单填方案的止损价，卖单没有止损价
  - `tests/modules/execution/test_service.py`：order-lifecycle spec「买入方案提交限价买单」补上"订单记下止损价 45"，「减仓方案提交市价卖单」补上"订单没有止损价"
  - `tests/modules/execution/test_config.py`：默认立即重试次数 1；配置为 -1 时报错

  验证：运行 `uv run pytest tests/modules/execution`，确认因为字段和配置项不存在而失败
- [x] 1.2 实现：
  - `domain/order.py`：`Order` 增加 `stop_price: Decimal | None`，`new_order` 填写
  - `config.py`：增加 `stop_immediate_retries`（默认 1，不小于 0）
  - `ports.py`：`Broker` 增加 `position`；`OrderStore` 增加 `of_symbol`；`Broker` 和 `OrderRejectedError` 的说明按 design D6、D10 改写

  验证：1.1 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
- [x] 1.3 测试替身（design D12）：
  - `tests/fakes/broker.py`：`FakeBroker` 增加 `positions`；`position` 记入调用，可以安排失败
  - `tests/fakes/orders.py`：`InMemoryOrderStore` 增加 `of_symbol`

  验证：全部已有测试通过

## 2. 止损保护的纯逻辑

- [x] 2.1 先写测试 `tests/modules/execution/domain/test_protection.py`：
  - `stop_price_of`：spec「止损价」三个场景，以及买入成交后加仓、再一股没成交的加仓
  - `stop_target`：spec「止损目标」五个场景，包括状态未知的卖单也扣除
  - `StopOrder` 和 `Protection` 按券商回报更新：已受理未结束、全部成交、撤销或失效、拒绝、结果未知（spec「对齐止损单」第一张表）；查不到时有券商订单号的记为状态未知，没有的记回还没挂出去
  - 记为状态未知、等待时长是否已满、下一张止损单的客户端订单 ID（`BBB-STOP-1`、`BBB-STOP-2`）

  验证：运行 `uv run pytest tests/modules/execution/domain`，确认因为模块不存在而失败
- [x] 2.2 实现 `domain/protection.py` 的数据类型、`stop_price_of`、`stop_target` 和按回报更新（design D1–D3、D9）。验证：2.1 的测试通过；`uv run pytest tests/architecture` 通过（纯区域的规则）
- [x] 2.3 先写测试，再实现 `next_action`、`stop_yields`、`backup_stop_due`（design D4、D5、D8）：
  - `next_action`：spec「对齐止损单」第二张表的每一行，包括挂着但止损价不同、目标为 0 时还没挂出去的放弃
  - `stop_yields`：spec「卖单先让出股数」的三种情况
  - `backup_stop_due`：spec「本地备用止损的触发」四个场景

  验证：`uv run pytest tests/modules/execution/domain` 通过，`uv run mypy` 通过

## 3. 对齐止损单

- [x] 3.1 准备：`tests/fakes/stops.py`（`FakeStopBroker`）和 `tests/fakes/protections.py`（`InMemoryProtectionStore`）；`ports.py` 增加 `StopBroker`、`ProtectionStore`；`ExecutionService` 的构造增加这两个依赖；`tests/modules/execution/builders.py` 的 `execution_service` 跟着改；"持有 BBB 100 股，止损单已对齐"的构造要用整套测试环境，放在 3.2 的服务测试里。验证：全部已有测试通过
- [x] 3.2 先写测试 `tests/modules/execution/test_protection_service.py`：
  - spec「止损单」两个场景、「止损价」三个场景
  - spec「对齐止损单」的场景：继续成交后改单、已经对齐时不动、改单的结果以查询为准、止损单成交后不再挂新的、卖单部分成交后过期、清仓没卖完时重新挂、还没挂出去的止损单在目标为 0 时放弃
  - spec「保存止损保护」两个场景

  验证：运行 `uv run pytest tests/modules/execution/test_protection_service.py`，确认因为 `protect` 不存在、`track` 不对齐而失败
- [x] 3.3 实现 `ExecutionService.protect`，`track` 在跟进之后对齐（design D4，以及 D5 的"跟进之后再对齐"）。验证：3.2 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check` 通过

## 4. 卖单先让出股数

- [x] 4.1 先写测试：
  - `test_protection_service.py`：spec「卖单先让出股数」四个场景
  - `test_service.py`：order-lifecycle spec「止损单还没让出股数时不提交卖单」

  验证：确认这几个场景失败
- [x] 4.2 实现 `track` 里卖单提交前的对齐和让出检查，以及同一次跟进里不重复对齐（design D5）；止损单状态未知、还没满等待时长时对齐不调用券商（让出检查依赖它，从 6.2 提前到这里）。验证：4.1 的测试和全部已有测试通过，包括订单状态机的性质测试

## 5. 被拒、无保护和备用止损

- [x] 5.1 先写测试 `test_protection_service.py`：
  - spec「挂单或改单被拒」五个场景
  - order-lifecycle spec「立即重试次数为 0」
  - spec「无保护时禁止加仓」四个场景
  - spec「本地备用止损的触发」四个场景，通过 `ExecutionService.backup_stop_due`

  验证：确认这几个场景失败
- [x] 5.2 实现立即重试、标记和解除无保护（design D6），`submit` 抛 `UnprotectedPositionError`（design D7），`ExecutionService.backup_stop_due`（design D8）；`__init__.py` 导出新的名字。验证：5.1 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 止损单的券商调用失败

- [x] 6.1 先写测试 `test_protection_service.py`：spec「止损单的券商调用失败」五个场景。验证：确认场景失败
- [x] 6.2 实现状态未知的处理：超时或断线记为状态未知，查不到时按有没有券商订单号处理，查询持仓失败时不做别的处理（design D9；没满等待时长不调用券商已经在 4.2 做了）。验证：6.1 的测试和全部已有测试通过

## 7. 性质测试和收尾

- [x] 7.1 `test_protection_service.py` 增加状态机性质测试（design D12）：随机交错买入成交、卖单提交和成交、改单或撤单生效、止损单触发、调用失败或被拒、重启、拨动时间，检查三条性质。验证：`uv run pytest tests/modules/execution` 通过
- [x] 7.2 对照两份 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 7.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
