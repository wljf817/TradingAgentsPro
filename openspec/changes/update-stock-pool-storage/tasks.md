# Tasks

## 1. 人的决定

- [ ] 1.1 人对 proposal「需要人决定」表态以后：同意推荐做法的照做——第 1 条不改本变更的代码，写进「方案接下单」；第 2 条改完整版「股票池与寻股」的字段表，加投资论点、上次买入升级或续期的那一天两行；第 3 条先在 stock-pool delta 里加"成为候选的时刻"（记录表一行；状态变化表里加入和代码触发的清仓设为当时，降为候选为空；对应的场景），再改 design D1 的字段和不变量、本文件 2.1；第 4 条不改。不同意的按人的决定先改 spec 和 design。验证：`openspec validate update-stock-pool-storage --strict` 通过，proposal、spec、design、tasks 的说法一致

## 2. 记录、状态变化和论点更新

- [ ] 2.1 先写测试（design D1、D2、D7）：`tests/modules/stockpool/builders.py` 加基准投资论点（从 `tests/modules/scheduler` 的 builders 复制），CCC 带它，BBB、CCC 的上次复查的那一天 2026-10-07，PM 发起的操作的案件 ID K1；`domain/test_entry.py` 检查每个状态变化的方法之后的字段和 D1 的不变量、两个论点更新的方法只换论点和上次复查的那一天、`bought` 对持仓返回原来的记录；`test_service.py` 覆盖 stock-pool spec「股票池的记录」「状态变化」「论点更新」改了和新增的场景，其余要求的测试只改调用的参数；`test_state_machine.py` 加两个论点更新、带论点的买入成交、两种 PM 发起的清仓，每一步之后检查 D1 的不变量。验证：确认新测试因为新字段、新方法和新参数不存在而失败
- [ ] 2.2 实现（design D1、D2）：`domain/entry.py` 把 `thesis` 改名 `watch_thesis`，加 `investment_thesis`、`last_review_day` 和两个论点更新的方法，改 `promoted`、`demoted`、`bought`、`closed_to_candidate`、`closed_to_watch`；`service.py` 加 `update_watch_thesis`、`update_investment_thesis`，`promote`、`bought` 带论点和上次复查的那一天，`closed_by_pm` 分成 `closed_to_watch`、`closed_and_removed`，PM 发起的操作加只能按名字传的 `case_id`；包根和模块的说明跟着改。验证：2.1 的测试和 `tests/modules/stockpool` 原有的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 3. 程序日志

- [ ] 3.1 先写测试（design D5、D7）：新建 `tests/modules/stockpool/test_program_log.py`，覆盖 program-log spec「股票池的动作」的全部场景（行为者、动作、对象、案件 ID，说明里有关键的值；写条目出错用 `append` 会抛错的程序日志存储）；builders 给用例一个 `ProgramLog`（`tests/fakes/program_log.py` 的内存存储，和用例同一个测试时钟）；`test_state_machine.py` 每一步之后检查条目数：通过并改变了记录的、被拒绝的请求各多一条，没改变的、报错的不多。验证：确认新测试因为构造参数和动作不存在而失败
- [ ] 3.2 实现（design D5）：`modules/programlog/domain/entry.py` 的 `Actor` 加 `STOCK_POOL`、`Action` 加十个 `POOL_*`；`StockPoolService` 的构造参数加 `log: ProgramLog`，每个改变记录的操作保存以后写条目、被拒绝时只写条目，拒绝原因的中文对照放在 `service.py`。验证：3.1 的测试通过；`tests/modules/programlog` 照旧通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. 同时进行的操作

- [ ] 4.1 先写测试（design D4、D7）：新建 `tests/modules/stockpool/test_concurrency.py`，覆盖 stock-pool spec「同时进行的操作」的三个场景：存储包一层，`in_pool`、`changed_since` 被调用时最多等 0.5 秒、等另一个线程也来读；两个线程各发一个请求，断言恰好一个通过、另一个的原因对，记录和条目数对。验证：确认没有锁时测试失败
- [ ] 4.2 实现（design D4）：`StockPoolService` 持有一把 `threading.Lock`，改变记录的操作和被拒绝的请求全程拿着它，`entry`、`entries` 不拿；`record_score` 找池子里的记录改用私有方法；去掉类说明里"由调用方串行调用"的话。验证：4.1 的测试通过，用 shell 循环连续跑 20 次都通过；`tests/modules/stockpool` 全部通过

## 5. 存储

- [ ] 5.1 先写测试（design D3、D7，要开着 Docker：`open -a OrbStack`）：`tests/modules/statestore/test_repo.py` 加 `latest_since`：只看每个键的最新版本、几个时刻列任一列不早于给定时刻就列出、等于给定时刻也算、为空的列不算、按键排序；新建 `tests/modules/stockpool/test_repo.py` 覆盖 stock-pool spec「股票池的存储」的场景，列出和 `changed_since` 内存实现和数据库实现都测，原样读回、重启、数据库连不上只测数据库实现；`tests/bootstrap/test_database.py` 的三个场景和 `test_app.py` 的表清单加上 `stockpool_entries`（列、类型、能不能为空、主键、属于股票池模块）。验证：确认新测试因为表和 `latest_since` 不存在而失败
- [ ] 5.2 实现（design D3、D6）：`VersionedStore` 把"每个键的最新版本"的子查询和读回抽成私有方法，加 `latest_since`，`latest` 的行为不变；新建 `modules/stockpool/repo.py`（表 `stockpool_entries`、`PostgresStockPoolStore`）；包根导出 `PostgresStockPoolStore`；`ports.py` 的说明改为写明数据库实现在 `repo.py`；新建迁移 `bootstrap/migrations/versions/v0005_create_stock_pool.py`（`revision = "0005"`，`down_revision = "0004"`，写明每一列，不 import 业务代码）。验证：5.1 的测试通过；`uv run pytest tests/architecture tests/modules/statestore tests/modules/stockpool tests/bootstrap` 通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 收尾和归档

- [ ] 6.1 对照本变更的三份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的要求里原有的场景仍有测试。验证：对应表写在完成报告里
- [ ] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate update-stock-pool-storage --strict`。验证：全部通过；`git status` 里没有 `tests/` 下的文件被暂存
- [ ] 6.3 归档前重新对齐 MODIFIED 的要求：MODIFIED 会整段替换主 spec 里的要求，而跟进存储、任务和费用存储可能先归档，改了 state-store「表的归属和迁移」（加它们的表）和 program-log「程序日志的条目」（加它们的行为者）。把这两个 MODIFIED 块换成主 spec 当时的全文，再加上本变更的改动（股票池表 `stockpool_entries`、行为者股票池）；stock-pool 的两个 MODIFIED 块也对照当时的主 spec 检查。program-log spec 的 Purpose 不随 delta 改，归档时直接在主 spec 的 Purpose 里把股票池加进行为者。然后归档。验证：`openspec validate update-stock-pool-storage --strict` 通过；归档以后主 spec 里别的变更加的表和行为者都还在，`openspec validate --all --strict` 通过
