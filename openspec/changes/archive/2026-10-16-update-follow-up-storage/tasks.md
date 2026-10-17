# Tasks

## 1. 人的决定

- [x] 1.1 人对 proposal「需要人决定」表态以后：同意推荐做法的不改；不同意第 1 条的，从 program-log 的 delta spec 和 design D4 去掉触发跟进，2.1、2.2 里触发跟进的测试和实现跟着去掉。验证：`openspec validate update-follow-up-storage --strict` 通过，proposal、spec、design、tasks 里说法一致

## 2. 程序日志的条目

- [x] 2.1 先写测试（design D4、D5、D8）：`tests/modules/followups/builders.py` 的 `follow_up_service` 加一个程序日志的内存存储参数（没有传入时新建），用例的程序日志用它和同一个时钟；`test_service.py` 的 `_Setup` 带上这个存储，覆盖 program-log spec「调度器的动作」的全部场景（行为者、动作、对象、案件 ID、引用，以及说明里场景写明的值）；`test_state_machine.py` 给用例程序日志，每一步之后检查：每个跟进正好一条批准跟进，已触发、被替换、已作废的各有一条对应动作的条目，待执行的没有，每次拒绝一条拒绝跟进。每个测试的说明写明对应的场景。验证：确认新测试因为构造参数、`Actor.SCHEDULER` 和新动作不存在而失败
- [x] 2.2 实现（design D4、D5、D6）：`modules/programlog/domain/entry.py` 加 `Actor.SCHEDULER` 和五个动作；新建 `modules/followups/domain/entries.py`（`content`、`rejection_detail`、`void_detail`）；`FollowUpService` 的构造参数最后加 `log: ProgramLog`，四个方法按 design D4 的表在保存以后写条目，每保存一个写一条；`domain/__init__.py`、`service.py` 的说明写明程序日志。验证：2.1 的测试通过；`tests/modules/followups`、`tests/modules/programlog` 原有的测试只改构造参数，全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. 跟进表和数据库实现

- [x] 3.1 先写测试（design D1、D2、D7、D8）：新建 `tests/modules/followups/test_repo.py`：端口的行为同时测内存实现和数据库实现（`pending()` 只看最新版本；`requested_since` 等于 moment 的算，已触发、被替换、已作废的也给出）；follow-ups spec「跟进的存储」的四个场景只测数据库实现（版本直接查表看版本号、状态和写入时刻；重启用同一个数据库新建 Engine、存储和用例；连不上用连不上的地址，审批、取得待执行的跟进、到点触发、状态变化都报错）。`tests/bootstrap/test_database.py` 按 state-store spec「表的归属和迁移」的三个场景加上跟进表：表清单、列和类型、主键、归属跟进审批模块；`tests/bootstrap/test_app.py` 的空数据库：表清单加跟进表，迁移版本是 `"0006"`。验证：要开着 Docker（`open -a OrbStack`）；确认新测试因为 `PostgresFollowUpStore` 和迁移不存在而失败
- [x] 3.2 实现（design D1、D2、D6、D7）：新建 `modules/followups/repo.py`（`FOLLOW_UPS`、`PostgresFollowUpStore`），包根导出 `PostgresFollowUpStore`；新建迁移 `bootstrap/migrations/versions/v0006_create_follow_ups.py`（`down_revision` 是股票池存储的 `"0005"`）；`ports.py`、`__init__.py` 的说明改成现在的样子（数据库实现在 `repo.py`）。验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过；`uv run pytest tests/architecture tests/modules/followups tests/bootstrap/test_database.py` 通过

## 4. 同时进行的调用

- [x] 4.1 先写测试 `tests/modules/followups/test_service_threads.py`（design D3、D8）：包住内存存储的替身，第一个调用读存储时等第二个调用也来读，最多等 0.5 秒；覆盖 follow-ups spec「同时进行的调用」的两个场景：两个审批在 `requested_since` 上等，一个获批、一个因申请次数已用完被拒绝；到点触发先进去、在 `pending()` 上等，结果是"到点触发在前"的那一种（F-1 已触发、没有被替换，F-2 待执行）。验证：确认两个测试在没有锁时失败
- [x] 4.2 实现（design D3）：`FollowUpService` 建一把 `threading.RLock`，`pending`、`request`、`trigger_due`、`status_changed` 的整个方法体都在锁里；`service.py` 的说明写明一次只进行一个，去掉"由调用方串行调用，做法 M4 再定"。验证：4.1 的测试通过；用 shell 循环连续跑 `test_service_threads.py` 20 次都通过；`tests/modules/followups` 全部通过

## 5. 收尾

- [x] 5.1 对照本变更的三份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的两个要求里原有的场景仍有测试。验证：对应表写在完成报告里
- [x] 5.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate update-follow-up-storage --strict`。验证：全部通过；`git status` 里没有 `tests/` 下的文件被暂存
- [x] 5.3 归档：MODIFIED 的要求整段替换主 spec 里的同名要求。state-store「表的归属和迁移」和 program-log「程序日志的条目」两段 delta 已经按股票池存储归档以后的主 spec 重写（保留股票池的表和行为者，再加上本变更的）；归档前再和主 spec 对一次，有别的改动就同样合进来。`openspec archive update-follow-up-storage -y` 以后，program-log spec 的 Purpose 不随 delta 改，直接在主 spec 里把调度器加进行为者；`docs/实现路线.md` 只改两格：「跟进存储」的状态改为完成，M4 的完成数改为 3 / 18。验证：`openspec validate --all --strict` 通过；主 spec 里有本变更的三处新增要求，修改的两处要求里股票池的内容还在
