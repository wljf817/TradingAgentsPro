# Tasks

## 1. 人的决定

- [x] 1.1 人对 proposal「需要人决定」表态以后：同意推荐做法的不改；不同意的先改 spec 和 design（第 1 条改 design D1 和 task-dispatch「任务的存储」，第 2 条只改 proposal 的 Non-goals 和 design D6）。验证：`openspec validate update-task-cost-storage --strict` 通过，proposal、spec、design、tasks 里说法一致

## 2. 表和迁移

- [x] 2.1 先写测试（design D1、D7）：`tests/bootstrap/test_database.py` 的「空数据库升级到最新版本」加 `gateway_tasks`、`gateway_results`、`gateway_costs` 的列、主键和两个索引，「表名以写入它的模块开头」加三张表属于 `gateway`；`tests/bootstrap/test_app.py` 的表的清单加三张表，最新迁移版本改为 `"0007"`。验证：要开着 OrbStack（`open -a OrbStack`）；确认新断言因为表不存在而失败
- [x] 2.2 实现：`modules/gateway/repo.py` 的三个表定义（design D1）；迁移 `bootstrap/migrations/versions/v0007_create_gateway_tables.py`，写明每一列和索引、不 import 业务代码，`down_revision = "0006"`（design D7）。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 任务的存储和一个 Result

- [x] 3.1 先写测试（design D2、D3、D4、D8）：`tests/fakes/tasks.py` 的 `add_result` 改为已经有 Result 时给出它、写入了给出 None；新建 `tests/modules/gateway/test_repo.py` 的任务部分：`PostgresTaskStore` 的往返、本案件的任务按写入的先后、没有这个任务时 `KeyError`、`add_result` 冲突时给出先写入的；用真的数据库跑 task-dispatch spec「任务的存储」的五个场景和「一个任务只有一个 Result」的「同时生成 Result」（两个线程，存储包一层，在 `record` 之后等对方也读完）；`test_database.py` 的「空数据库升级到最新版本」补上用 `PostgresTaskStore` 追加和读取；`tests/modules/gateway/test_service.py` 补「同时生成 Result」的内存版（存储包一层，在 `record` 之后插入另一个 Result）。每个测试的说明写明对应的场景。验证：确认因为 `PostgresTaskStore` 不存在、`add_result` 不给出已有的 Result 而失败
- [x] 3.2 实现：`repo.py` 的 `PostgresTaskStore`（design D2、D3）；`ports.py` 的 `TaskStore` 说明和 `add_result`；`service.py` 的 `_settle` 和 `TaskDispatchService` 的说明（design D4，去掉"做法 M4 定"）；`__init__.py` 导出 `PostgresTaskStore`，说明写明 Task 和 Result 存在哪两张表。验证：3.1 的测试通过；`tests/modules/gateway` 原有的测试全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. 费用的存储

- [x] 4.1 先写测试（design D2、D5、D8）：`test_repo.py` 的费用部分：`PostgresCostStore` 的往返（费用的每一位小数，进程在 UTC+14 时记下的时刻读回同一时刻）、按写入的先后、记下的时刻正好是 moment 的记录算在 `since` 里；用真的数据库跑 call-budget spec「费用的存储」的「原样读回」「读写出错」「同时记下都写入」（十个线程）和「费用的记录」的「重启以后接着算」；`test_database.py` 的「空数据库升级到最新版本」补上用 `PostgresCostStore` 追加和读取；`tests/modules/gateway/test_budget_service.py` 补「同时记下的几条都记普通预算」（两个线程，内存存储包一层，在 `since` 里等对方也读完；记账的代码不改，这条测试直接通过）。验证：确认数据库部分因为 `PostgresCostStore` 不存在而失败
- [x] 4.2 实现：`repo.py` 的 `PostgresCostStore`（design D2）；`ports.py` 的 `CostStore` 说明；`service.py` 的 `CallBudgetService` 说明（design D5，去掉"做法 M4 定"）；`__init__.py` 导出 `PostgresCostStore`，说明写明费用存在哪张表。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 程序日志的条目

- [x] 5.1 先写测试（design D6、D8）：`tests/modules/gateway/builders.py` 的 `task_service` 加程序日志的内存存储；`test_service.py` 覆盖 program-log spec「Agent 网关的动作」的七个场景（断言行为者、动作、对象、案件 ID、引用、耗时、token、费用，说明里有场景写明的值），以及 3.1 的「同时生成 Result」内存版不写条目；`test_repo.py` 的「同时生成 Result」补断言 T-1 只有一条生成 Result；`test_state_machine.py` 的构造参数加程序日志，每一步之后检查每个 Task 正好一条派发任务或拒绝任务、派发出去的 Task 有 Result 时正好一条生成 Result。验证：确认因为 `Actor.GATEWAY` 不存在、`TaskDispatchService` 不收程序日志而失败
- [x] 5.2 实现：`modules/programlog/domain/entry.py` 加 `Actor.GATEWAY` 和三个 `Action`（design D6）；新建 `modules/gateway/domain/entries.py` 的三个说明函数；`service.py` 的 `TaskDispatchService` 构造参数加 `ProgramLog`，`dispatch` 和 `_settle` 写条目；`modules/gateway/__init__.py`、`domain/__init__.py` 的说明加上程序日志的条目。验证：5.1 的测试通过；`tests/modules/gateway`、`tests/modules/programlog`、`tests/architecture` 通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 收尾

- [x] 6.1 对照本变更的四份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的要求里原有的场景仍有测试。验证：对应表写在完成报告里
- [x] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate update-task-cost-storage --strict`。验证：全部通过（要开着 OrbStack）；`git status` 里没有 `tests/` 下的文件被暂存
- [x] 6.3 归档前：别的变更（股票池存储、跟进存储等）先归档、改过本变更 MODIFIED 的要求的，按当时的主 spec 重写对应的 delta（state-store「表的归属和迁移」、program-log「程序日志的条目」、task-dispatch「一个任务只有一个 Result」），只加本变更的内容；确认 v0007 的 `down_revision` 是当时最新的版本、`test_app.py` 的最新版本是 `"0007"`。验证：`openspec validate update-task-cost-storage --strict` 通过；归档后的主 spec 里别的变更加的表、行为者和场景都还在
