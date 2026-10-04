# Tasks

## 1. 订单上的撤单要求

- [x] 1.1 先写测试 `tests/modules/execution/domain/test_order.py`（design D1）：
  - `want_cancel`：未结束的订单记下撤单要求，其他字段不变；终态的订单原样返回
  - `cancel_due`：已提交、部分成交的订单，有撤单要求时截止时刻之前也为真；已经请求过撤单时为假；待提交、状态未知时为假；没有撤单要求时和原来一样
  - `cancel_unsent`：结束为已撤销，累计成交 0；终态的订单原样返回

  验证：运行 `uv run pytest tests/modules/execution/domain/test_order.py`，确认因为字段和方法不存在而失败
- [x] 1.2 实现 `domain/order.py` 的 `cancel_wanted`、`want_cancel`、`cancel_unsent`，`cancel_due` 加上撤单要求。验证：1.1 的测试和全部已有测试通过；`uv run mypy` 通过

## 2. 提前撤单

- [x] 2.1 先写测试 `tests/modules/execution/test_cancel_service.py`。和其他执行测试文件一样，自己建一套执行测试环境（存储、假券商、假止损单接口、测试时钟，新建后先对账一次）。每个 spec 场景一个测试：
  - 「提前撤单」十四个场景。「撤掉还在等止损单让出股数的卖单」按 stop-protection spec 的约定安排止损单，并检查止损目标恢复为 100 股
  - 「保存订单」的「重启后继续提前撤单」

  验证：运行这个测试文件，确认因为 `cancel_orders`、`cancel_buy_orders` 不存在而失败
- [x] 2.2 实现 design D2、D3：`_track` 对有撤单要求的卖单不先对齐止损单；`_advance` 对没受理过又查不到、截止时刻之前有撤单要求的订单调用 `cancel_unsent`，不提交；`ExecutionService` 增加 `cancel_orders`、`cancel_buy_orders`，先全部保存撤单要求再逐笔跟进。验证：
  - 2.1 的测试和全部已有测试通过，包括「先查询再提交」「券商调用失败时状态未知」「保存订单」原有的场景
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 性质测试

- [x] 3.1 `tests/modules/execution/test_service.py` 的订单状态机（design D5）：增加"拨动时间后撤掉 BBB 的订单"这一步；增加不变式"有撤单要求以后，券商不再收到 P1 的提交"。验证：`uv run pytest tests/modules/execution/test_service.py` 通过，并且确认样例里出现过提前撤单后结束为已撤销（用 `event` 统计）
- [x] 3.2 `tests/modules/execution/test_protection_service.py` 的止损保护状态机：增加"撤掉 BBB 的订单"这一步。验证：`uv run pytest tests/modules/execution/test_protection_service.py` 通过，并且确认样例里出现过等让出的卖单被撤（用 `event` 统计）

## 4. 文档和收尾

- [x] 4.1 人同意 proposal「需要人决定」第 1 条之后，在完整版 Proposal「订单生命周期」表"主动减仓或清仓"一行后面增加"紧急风险退出"一行。验证：这一行和推荐做法一致，其他行没有改动
- [x] 4.2 对照 delta spec，确认每个新增场景都有对应的测试，测试名或说明里写明场景；修改过的三条要求，原有场景的测试仍然通过。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 4.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
