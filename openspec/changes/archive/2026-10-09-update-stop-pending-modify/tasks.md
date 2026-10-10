# Tasks

## 0. 前提

- [x] 0.1 「收盘对账」已经落到 main（e3d3dd5）；「下单流程」还在别的 worktree 里实施，它只改 `workflows/` 和 `bootstrap/`、不改止损保护，协调人确认不用等它（2026-10-09）。在最新的 main 上实施

## 1. 先写测试

- [x] 1.1 `tests/modules/execution/domain/test_protection.py`（design D1、D2、D4、D5）：
  - `StopOrder.request(quantity, now)` 记下请求的股数和时刻，券商回报的股数和止损价不变
  - `apply`：挂着的回报股数等于请求的股数时，`requested` 和 `requested_at` 一起清掉；不等时都保留；止损单结束时一起清掉
  - `awaits_modify(now, wait)`：没有请求时为假；有请求、没有时刻（旧记录）时为假；挂着、请求时刻 15:00Z、等待 30 秒时，15:00:29Z 为真，15:00:30Z 为假；不是挂着（状态未知、已经结束）时为假
  - `next_action(protection, target, now, wait)`：等待生效时，目标股数不同、只有请求的股数和目标不同、目标为 0、没有止损价都给出不动；满等待时长以后，目标不同给出改单，目标为 0 给出撤单；没有请求时和原来一样
  - 已有的 `next_action`、`request` 测试改用新的参数，有改单请求的那几个取满等待时长以后的时刻，期望不变

  验证：运行这个测试文件，确认因为参数、字段和方法不存在而失败
- [x] 1.2 `tests/modules/execution/test_protection_service.py`，每个 spec 场景一个测试，测试名或说明里写明场景：
  - 「对齐止损单」：按改写后的「改单的结果以查询为准」改写原来的 `test_the_modify_result_comes_from_the_next_lookup`；新增「改单请求满等待时长还没生效，再改一次」「券商一直没完成改单，再改单被拒时标记无保护」
  - 「卖单先让出股数」：改写「减仓时先改小止损单，券商确认后再提交卖单」（加上时刻和"没有再改单"）、「改大的请求还没生效时不提交卖单」；新增「清仓时改单请求还没生效，先不撤止损单」
  - 「止损单的券商调用失败」：「改单超时，券商没有收到」
  - 「保存止损保护」：「重启后改单请求仍在等待生效」

  验证：运行这个测试文件，确认检查"等待生效时不改单、不撤单"的测试失败，原因是请求还没生效时又收到了改单或撤单；「券商一直没完成改单，再改单被拒时标记无保护」「改单超时，券商没有收到」检查的是满等待时长以后照常再改单，现在就通过；其余已有测试仍然通过
- [x] 1.3 `tests/modules/execution/test_reconciliation_service.py`：stop-protection spec「对账时跟进订单改了单，对齐这只股票时不再改单」，用 `ExecutionService.reconcile()`。验证：运行这个测试，确认因为收到两次改单而失败

## 2. 实现

- [x] 2.1 实现 design D1、D4、D6：`domain/protection.py` 增加 `StopOrder.requested_at`（放在 `requested` 后面，默认 None），`request` 增加 `now` 参数，`apply` 清 `requested` 时一起清 `requested_at`，新增 `awaits_modify`，`next_action` 增加 `now`、`wait` 参数并在等待生效时给出不动；`service.py` 的 `_act` 把当前时刻和 `unknown_lookup_delay` 交给 `next_action`，`_modify_stop` 把当前时刻交给 `request`。同时更新说明：`protection.py` 的模块说明和 `next_action`、`_act`、`_modify_stop` 的说明写上等待生效和记下时刻；`config.py` 里 `unknown_lookup_delay` 的注释写上"止损单的改单请求发出以后，等这么久还没生效才再改单或撤单"（design D3）。验证：第 1 组的测试全部通过；`uv run pytest tests/modules/execution tests/workflows tests/integrations/alpaca` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

- [x] 2.2 先写测试 `tests/integrations/alpaca/test_stops.py`：止损单 O2 正在改单（pending_replace）时撤单，Alpaca 返回 422，适配器正常返回，O2 仍是 pending_replace（alpaca-broker spec「撤单」：撤不了时正常返回）；再改 `tests/integrations/alpaca/server.py`，FakeAlpaca 对 pending_replace 的订单撤单返回 422、不改状态（Alpaca 文档：pending_replace 的订单拒绝撤单）。验证：改 FakeAlpaca 之前这个测试因为状态变成 pending_cancel 而失败，改之后通过；`uv run pytest tests/integrations/alpaca tests/bootstrap` 通过

## 3. 存储

- [x] 3.1 `tests/modules/execution/test_repo.py`：有改单请求股数和时刻的止损保护存进数据库再读出来不变；存进数据库以后在 JSONB 里去掉 `requested_at`（和「买单和止损单错开」的旧记录测试一样），读出来 `requested_at` 为 None、`awaits_modify` 为假（design D6）。验证：开着 Docker 运行这个测试文件，通过

## 4. 性质测试

- [x] 4.1 `tests/modules/execution/test_protection_service.py` 的 `StopProtectionMachine` 增加一条规则"接着对齐两次"（design D7）：不拨时间、不让请求生效，对齐 BBB 两次；第一次期间 `FakeStopBroker.modify_requests` 变长（有被接受的改单请求）时，断言第二次期间 `FakeStopBroker.calls` 里没有新的 modify 和 cancel。`StaggeredStopMachine` 继承它。验证：`uv run pytest tests/modules/execution/test_protection_service.py` 通过，两个状态机已有的不变式和结束时的检查都通过；用 `event` 统计，确认样例里出现过"第一次对齐有改单请求"的情况；临时去掉 `next_action` 里等待生效的判断时这条断言会失败，确认后恢复

## 5. 收尾

- [x] 5.1 对照 delta spec，确认每个新增和改写的场景都有对应的测试；修改过的四条要求，原有场景的测试仍然通过。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 5.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
