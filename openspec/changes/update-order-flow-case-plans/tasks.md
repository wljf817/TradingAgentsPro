# Tasks

## 1. 前提

- [ ] 1.1 核对「案件工作流」已经按 design「前提」归档：`workflows/plan_handoff.py` 有 `PlanHandoff`、`Handoff(decision, confirming)`、`PlanEvent`、`PLAN_TOPIC`、`report_plan`；`case_workflow.py` 在工作流函数里调用 `submit`（不在 step 里）；`CaseState.risk_checked` 接收 `confirming`；`openspec/specs/case-workflow/` 存在。验证：`grep -n "class Handoff\|def report_plan" src/tradingagentspro/workflows/plan_handoff.py` 和 `grep -n "confirming" src/tradingagentspro/modules/scheduler/domain/stage.py` 都有结果；任何一项不对就停下，报告给人，不在本变更里补

## 2. 执行模块的来源案件

- [ ] 2.1 先写测试（design D5）：`tests/modules/execution/domain/test_order.py`、`test_trade.py` 加 order-lifecycle spec「案件的方案记下来源案件」、trade-records spec「案件的方案的交易记录带来源案件」「加仓的案件换成止损单的来源案件」和基准场景里的"没有来源案件"；`tests/modules/execution/test_service.py` 加 `submit(..., case_id=)`、`held_symbols`（超时抛 `BrokerUnavailableError`）、`orders(symbol)` 按创建的先后；`tests/modules/execution/test_repo.py` 加旧记录（JSON 里没有 `case_id`）读回为 None（要开着 OrbStack）。验证：确认因为 `case_id`、`held_symbols`、`orders` 不存在而失败
- [ ] 2.2 实现（design D5）：`Order.case_id`、`new_order` 的 `case_id`；`Trade.case_id`、`trade_of_order`、`trade_of_stop(stop, source, calendar)`；`ExecutionService.submit(plan, decision, *, case_id)`、`held_symbols`、`orders`；`workflows/order_flow.py` 和 `workflows/emergency_flow.py` 的调用先传 `case_id=None`。验证：2.1 的测试通过；`uv run pytest tests/modules/execution tests/workflows` 原有的测试只改调用参数，全部通过；`uv run mypy`、`uv run lint-imports` 通过

## 3. 确认消息的意见

- [ ] 3.1 先写测试（design D4）：`tests/integrations/discord/test_messages.py` 覆盖 discord-notification spec「确认消息」新加的四个场景（开仓方案的双方意见、退出方案有异议、Critic 不可用、意见太长时截短）和"没有案件的意见"；`tests/modules/notification/test_service.py` 加 `request_confirmation` 把意见原样交给通知通道；`tests/fakes/notifier.py` 记下请求里的意见。验证：确认因为 `Disputed`、`Unreviewed`、`note` 不存在而失败
- [ ] 3.2 实现（design D4）：`modules/notification/domain/confirmation.py` 的 `Disputed`、`Unreviewed`、`CaseNote`，`ConfirmationRequest.note`；`service.py` 的 `request_confirmation(plan, decision, note)`；`__init__.py` 导出；`integrations/discord/messages.py` 写意见、和告警共用截短；`order_flow.py` 先传 `note=None`。验证：3.1 的测试通过，`tests/integrations/discord`、`tests/workflows` 原有的测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 4. /plan 的板块

- [ ] 4.1 先写测试（design D8）：`tests/integrations/discord/test_gateway.py` 覆盖 manual-plans spec「板块的选项」和「buy 的字段」里 sector 是选项；`test_plans.py` 改「填全了的买入」「加仓」「股票代码和板块的空白」「买入不查持仓」的期望（方案的板块是选项的值）。验证：确认失败
- [ ] 4.2 实现（design D8）：`plans.py` 的中文板块名表，`gateway.py` 的 `buy`、`add` 用 `app_commands.choices`，说明改为「板块」；`build_plan` 去掉板块的空白和为空的检查。验证：4.1 的测试和 `tests/integrations/discord` 原有的测试通过；`uv run mypy` 通过

## 5. 下单流程：案件的方案

- [ ] 5.1 先写测试（design D1、D4、D6）`tests/workflows/test_order_flow.py`：order-flow spec「案件的方案」的六个场景（用 `OrderFlow.check_case_plan` 直接调用）；「请求人工确认」新加的三个场景（附双方意见、注明 Critic 不可用、退出方案的确认请求发不出去要告警）；「确认的结果」「告警」新加的超时场景；program-log spec「案件的方案的风控检查」「自动执行」；`attempt` 把来源案件交给执行模块。确认规则的开关从 `NotificationConfig` 读，不在测试里硬编码。验证：确认因为 `check_case_plan`、`CaseSource` 不存在而失败
- [ ] 5.2 实现（design D1、D2、D4、D6）：`modules/programlog/domain/entry.py` 加 `Action.AUTO_EXECUTE` 和中文名；`order_flow.py` 的 `CaseSource`、`CaseCheck`、`check_case_plan`（和 `check_plan` 共用读账户、检查和风控检查的条目），`OrderFlow` 构造参数加 `NotificationConfig`、`CaseRecordService`，`request_confirmation`、`settle`、`attempt` 接收 `source`，自动执行和确认超时没有执行的文字；模块说明写上案件的方案。验证：5.1 的测试通过，`tests/workflows` 原有的下单流程测试只改构造和调用参数，全部通过；`uv run mypy`、`uv run lint-imports` 通过

## 6. 下单工作流：交接和送回

- [ ] 6.1 先写测试（design D1、D3、D10）`tests/workflows/test_order_workflow.py`（真的 DBOS，要开着 OrbStack）：测试里定义一个收结果的工作流当作案件，在测试的工作流函数里调用 `CasePlanHandoff.submit`；order-flow spec「结果送回案件」的十一个场景，「案件的方案」里交接返回的结果（要不要确认、风控拒绝不起下单流程、读券商失败为 None），重放时不再检查（照现有的 `Crash`），自动执行的方案按按钮时回调报错。验证：确认因为 `CasePlanHandoff`、`order_plan` 的新参数不存在而失败
- [ ] 6.2 实现（design D1 到 D3）：`order_workflow.py` 的 `order_plan(plan, decision, source)`、`_run`、`_executed`、`_report`、`except Exception` 送回出错，step `order_plan.check_case_plan`，`CasePlanHandoff`；`OrderPlanIntake.submit` 传 `source=None`；模块说明写上交接和送回。验证：6.1 的测试通过，原有的下单工作流测试全部通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 7. 股票池跟上持仓

- [ ] 7.1 先写测试（design D7）`tests/workflows/test_pool_sync.py`：`pool_change` 覆盖 pool-position-sync spec「股票池跟上持仓」表的每一行；`PoolSync` 用 `tests/fakes/` 的股票池、跟进、案件记录和券商、订单的内存实现，覆盖两个要求的全部场景（巡检的一轮用 `SessionPatrol.run_round`）；`tests/workflows/test_patrol.py` 加 session-patrol spec「股票池跟上持仓在第 3 步之后」。验证：确认因为 `workflows/pool_sync.py` 不存在而失败
- [ ] 7.2 实现（design D7）：`workflows/pool_sync.py` 的 `PoolChange`、`pool_change`、`PoolSync`（`targets`、`follow`）；`patrol.py` 的 `SessionPatrol` 加 `pool` 参数，第 3 步之后一项列出、每只股票一项；模块说明写上这一项。验证：7.1 的测试通过，`tests/workflows` 原有的巡检测试只改构造参数，全部通过；`uv run mypy`、`uv run lint-imports` 通过

## 8. 装配

- [ ] 8.1 先写测试（design D9）`tests/bootstrap/test_app.py`：bootstrap spec「巡检里股票池跟上持仓」；`TradingApp` 有股票池、跟进和案件记录。验证：确认失败
- [ ] 8.2 实现（design D9）：`bootstrap/app.py` 建股票池、跟进、案件记录，交给 `OrderFlow` 和 `PoolSync`，一份 `NotificationConfig` 给三处；`TradingApp` 加三个字段；模块说明写上。验证：8.1 的测试和 `tests/bootstrap` 原有的测试通过

## 9. 收尾

- [ ] 9.1 对照本变更的九份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的要求里原有的场景仍由原来的测试覆盖。验证：对应表写在完成报告里
- [ ] 9.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
