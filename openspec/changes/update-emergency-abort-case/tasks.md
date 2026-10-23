# Tasks

## 1. 前提

- [x] 1.1 核对 add-queue-runner 已经归档、代码在 main 上（design「Context」）：`case_workflow.running_cases`、`_PLAN_EVENTS[PlanEvent.VOIDED]`、`order_workflow.void_plan`、下单流程在等确认、等开盘、等重试时收作废、`queue_runner.DbosCases`、`EmergencyFlow.exiting`。验证：`openspec list` 里没有 add-queue-runner，上面的名字都能在 `src/` 里找到；任何一项不在就停下，报告给人
- [x] 1.2 核对 DBOS 的用法（design D4、D5）：在 `tests/workflows/test_case_workflow.py` 的真的 DBOS 里（要开着 OrbStack）确认工作流里 `DBOS.recv(topic, timeout_seconds=0)` 没有消息时立即交回 None、重放时交回同样的值；先送到的消息先收；`DBOS.send` 给已经结束的工作流不报错。验证：一个临时的测试通过，结论写进 1.2 的完成说明；任何一项不对就停下，报告给人。完成说明：临时测试通过（真的 DBOS）：没有消息时 `recv(timeout_seconds=0)` 立即交回 None；先送的 a、b 依次收到；进程在记下结果以后、工作流结束以前退出，重启以后三次 recv 交回同样的 None、a、b；给已经结束的工作流 send 不报错

## 2. 紧急退出的决定的参数

- [x] 2.1 先改测试（design D6）：`tests/modules/scheduler/domain/test_queue.py` 的「紧急退出」场景和 `test_queue_machine.py` 改为只交运行中的案件和方案。验证：确认因为 `emergency_decision` 的参数不对而失败
- [x] 2.2 实现（design D6）：`modules/scheduler/domain/queue.py` 的 `emergency_decision(running, plans, symbol)`，docstring 跟着改。验证：2.1 的测试和 `tests/modules/scheduler` 全部通过；`uv run mypy` 通过

## 3. 下单流程按原因作废

- [x] 3.1 先写测试（design D5）：`tests/workflows/test_order_flow.py` 覆盖 `settle`、`void_queued` 两种原因的释放原因和告警（中止的不告警，预留已经释放时不写释放预留）；`tests/workflows/test_order_workflow.py`（真的 DBOS）覆盖 order-flow spec「案件中止时作废它的方案」的六个场景和「结果送回案件」的「案件中止交回的方案不送回」，包括交回以后的步骤出了意外的错误也不送回出错；原有的排队运行作废的场景改为带 `VoidCause.NEW_CASE`。验证：确认因为 `VoidCause` 不存在而失败
- [x] 3.2 实现（design D5）：`order_flow.py` 的 `VoidCause`，`settle`、`void_queued` 的 `cause`；`order_workflow.py` 的 `void_plan(plan_id, cause)`、解码作废的消息、收到 `ABORTED` 以后不再送回、`CasePlanHandoff.abandon`；`plan_handoff.py` 的 `PlanHandoff.abandon`；`queue_runner.py` 的 `DbosCases.void` 写明 `NEW_CASE`；模块说明写上中止的作废。验证：3.1 的测试和原有的 `tests/workflows` 测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 4. 案件工作流收中止

- [x] 4.1 先写测试（design D4、D6）`tests/workflows/test_case_workflow.py`（真的 DBOS）：case-workflow spec「紧急退出中止」的八个场景、「交给下单」的「等的时候收到中止」；交回用记下 `abandon` 调用的 `PlanHandoff` 替身，中止用 `abort_case`；`queue_view` 给出运行中的案件和有阶段的方案。验证：确认因为 `PlanEvent.ABORTED`、`abort_case`、`queue_view` 不存在而失败
- [x] 4.2 实现（design D4、D6）：`plan_handoff.py` 的 `PlanEvent.ABORTED`；`case_workflow.py` 的 `_PLAN_EVENTS[PlanEvent.ABORTED]`、`_aborted` 和各处的调用、`_deliver` 等待里收到中止时 `abandon`、`abort_case`、`queue_view`，模块说明写上中止；`queue_runner.py` 的 `run_round` 改用 `queue_view`。验证：4.1 的测试、原有的案件工作流测试和 `tests/workflows/test_queue_runner.py` 通过；`uv run mypy` 通过

## 5. 紧急退出流程中止案件

- [x] 5.1 先写测试（design D1、D7、D8）`tests/workflows/test_emergency_abort.py`（新）：emergency-exit-flow spec「中止这只股票的案件」的十个场景，用 `tests/fakes/cases.py` 的 `FakeCases`（加 `abort`，记下中止的案件 ID 和相对程序日志的先后）；`tests/workflows/builders.py` 建 `EmergencyFlow` 时交 `FakeCases`；原有的紧急退出测试只改构造参数。验证：确认因为 `EmergencyFlow` 没有 `cases` 参数而失败
- [x] 5.2 实现（design D1、D7、D8）：`emergency_flow.py` 的 `RunningCases`、`EmergencyFlow` 的 `cases`、`advance` 开始那一段取得决定和交出中止、`start_text` 写中止的案件，模块说明写上；`queue_runner.py` 的 `DbosCases.abort`。验证：5.1 的测试和原有的 `tests/workflows/test_emergency_*.py`、`test_patrol*.py` 通过；`uv run mypy`、`uv run lint-imports` 通过

## 6. 装配

- [x] 6.1 先写测试（design D7）`tests/bootstrap/`：替身的案件在等人工确认时这只股票紧急退出，案件结束：紧急退出中止，确认消息已作废、没有有效预留。验证：确认失败
- [x] 6.2 实现（design D7）：`bootstrap/app.py` 先建一个 `DbosCases()`，交给 `EmergencyFlow` 和 `QueueParts`。验证：6.1 的测试和 `tests/bootstrap` 原有的测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 7. 收尾

- [ ] 7.1 对照本变更的三份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的要求里原有的场景仍由原来的测试覆盖。验证：对应表写在完成报告里
- [ ] 7.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
