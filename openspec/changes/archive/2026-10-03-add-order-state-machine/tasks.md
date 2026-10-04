# Tasks

## 1. `OutsideSessionError` 移到 shared

- [x] 1.1 按 design D10 移动：
  - `shared/trading_calendar.py` 增加 `OutsideSessionError(RuntimeError)`，说明写"在常规交易时段之外调用了只能在盘中做的操作"
  - `modules/risk/domain/recheck.py` 删除这个类；`recheck.py` 和 `service.py` 改为从 shared import
  - `modules/risk/__init__.py` 不再导出它
  - `tests/modules/risk/test_service.py` 改为从 shared import

  这一步不改变行为，已有的「开盘前调用时报错」测试就是它的测试。验证：
  - `uv run pytest` 全部通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
  - `grep -rn OutsideSessionError src tests` 只指向 shared 的定义

## 2. 执行配置和订单的纯逻辑

- [x] 2.1 先写测试：
  - `tests/modules/execution/test_config.py`：spec「默认配置」「等待时长不大于 0」
  - `tests/modules/execution/domain/test_order.py`，直接构造方案、风控结果、交易时段、订单和券商回报（design D3、D5）：
    - `new_order`：
      - 四种动作的买卖方向
      - 委托股数取风控结果的最终股数，订单类型和限价取方案的
      - 截止时刻：有效期早于收盘、晚于收盘、提前收盘的交易日
      - 创建时刻；状态是待提交；客户端订单 ID 是 `str(plan_id)`
    - `apply`：
      - spec「订单状态和券商回报」表的每一行，包括撤销回报在截止时刻前 1 秒和正好在截止时刻
      - 报结果未知时记下时刻，从状态未知恢复时清掉
      - 累计成交变少的回报被忽略；终态原样返回
    - `mark_unknown`、`reject`、`expire_unsent`、`request_cancel`：
      - 状态和字段的变化
      - `mark_unknown` 保留券商订单号和累计成交
      - 终态原样返回
    - 判断：
      - `lookup_due`：满等待时长前 1 秒和正好满时长；不是状态未知时总是到期
      - `cancel_due`：截止时刻前 1 秒和正好在截止时刻；部分成交；已经请求过；状态未知时不撤单
      - `blocks_new_orders`：只有待提交和状态未知
      - `is_terminal`：只有四种终态

  验证：运行 `uv run pytest tests/modules/execution`，确认因为模块不存在而失败
- [x] 2.2 实现（design D3、D5、D8）：
  - `modules/execution/config.py`：`ExecutionConfig`
  - `modules/execution/domain/__init__.py`
  - `modules/execution/domain/order.py`：`OrderSide`、`OrderStatus`、`Order`、`BrokerOrderStatus`、`BrokerOrder`、`OrderPausedError`、`new_order`
  - `modules/execution/__init__.py`：模块说明，导出上面这些

  验证：
  - 2.1 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
  - `uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库，模块内只有规定的文件）

## 3. 提交和跟进的正常路径

- [x] 3.1 端口和测试替身（design D4、D11）：
  - `modules/execution/ports.py`：`Broker`、`OrderStore`、`BrokerUnavailableError`、`OrderRejectedError`，由包根导出
  - `tests/fakes/orders.py`：`InMemoryOrderStore`
  - `tests/fakes/broker.py`：`FakeBroker`，包括调用记录、`update`、`fail_next(operation, after_effect)`、`reject_next_place`、`lose`，以及只记下请求的 `cancel`
  - `tests/modules/execution/builders.py` 的基准方案和基准结果，以及加仓、减仓、清仓方案的构造，已在 2.1 写好

  验证：`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
- [x] 3.2 先写测试 `tests/modules/execution/test_service.py`，用 `FakeClock` 拨动时间：
  - `builders.py` 增加 `execution_service(store, broker, clock, config)`：参数标注为 `OrderStore` 和 `Broker`，3.3 跑 mypy 时就会检查替身满足端口
  - spec「提交订单」的 8 个场景
  - spec「订单状态和券商回报」的 7 个场景
  - spec「先查询再提交」的 5 个场景
  - spec「到期撤单」的 4 个场景
  - spec「保存订单」的 2 个场景："重启"用同一个存储和假券商重新创建 `ExecutionService`
  - 进程在提交前后退出的场景：直接往存储里放待提交的订单

  验证：运行，确认因为 `ExecutionService` 不存在而失败
- [x] 3.3 实现 `ExecutionService.submit` 和 `track`（design D6、D7）。这一步先不处理 `BrokerUnavailableError`，也不做暂停检查：
  - `submit`：已有订单时跟进一次；新订单先检查交易时段，以待提交保存，再跟进
  - `track`：
    - 终态直接返回
    - 查询券商，按回报更新；没有券商订单号又查不到时，截止前提交、截止后记为已过期；拒绝受理时记为已拒绝
    - 到期时请求撤单
    - 有变化时保存
  - 包根导出 `ExecutionService`

  验证：
  - 3.2 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. 状态未知和暂停

- [x] 4.1 先写测试，加在 `tests/modules/execution/test_service.py`：
  - spec「券商调用失败时状态未知」的 7 个场景
  - spec「状态未知时暂停这只股票的新订单」的 5 个场景
  - spec「修改等待时长」

  验证：运行，确认因为没有处理调用失败、没有暂停检查而失败
- [x] 4.2 实现（design D6、D7、D8）：
  - `track` 捕获 `BrokerUnavailableError`，在已经得到的结果上 `mark_unknown`
  - 受理过却查不到的订单 `mark_unknown`
  - 状态未知且没满等待时长时不调用券商
  - `submit` 新建订单前，如果这只股票有其他订单 `blocks_new_orders`，抛 `OrderPausedError`

  验证：
  - 4.1 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
- [x] 4.3 状态机性质测试：在 `test_service.py` 增加 Hypothesis `RuleBasedStateMachine`，操作和不变量见 design D11。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/execution` 通过
  - 用 `event()` 统计生成的样例。要有相当比例的样例覆盖这几种情况：部分成交后到期、提交失败后恢复（生效前和生效后两种）、撤单失败后恢复、重启
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞，就先更新 spec 和 design，再改代码

## 5. 整体检查

- [x] 5.1 按 CI 的顺序在本地完整跑一遍：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1 validate --all --strict --no-interactive`。验证：
  - 全部通过
  - 逐条核对 order-lifecycle spec 的每个场景都有对应的测试
  - `git status` 里没有探针文件和缓存目录残留
