# Tasks

## 1. 人的决定

- [x] 1.1 proposal「需要人决定」的七条已经定了（2026-10-17，都按推荐做法；快照 ID 去掉），已经写进 proposal、spec、design 和完整版 Proposal「案件记录」两处。验证：`openspec validate add-case-records --strict` 通过，proposal、spec、design、tasks 里说法一致

## 2. 结论的联合类型

- [x] 2.1 先写测试（design D7）：`tests/contracts/test_conclusion.py` 加往返测试：四种结论的每一种决定（持仓的持有、加仓、减仓、清仓转为关注和移出，关注的开仓方案、继续关注、降为候选、移出，候选的升级、保持、移出，寻股的有增删和没有增删）按 JSON 写出、用 `TypeAdapter(Conclusion)` 读回，种类和内容都和写入的相同。验证：确认候选决定移出的那一例因为读成关注的结论而失败
- [x] 2.2 实现：`contracts/conclusion.py` 的 `Conclusion` 改为持仓、候选、关注、寻股的顺序，加上 design D7 的注释。验证：2.1 的测试通过；`tests/contracts`、`tests/modules/scheduler` 原有的测试全部通过；`uv run mypy`、`uv run ruff check` 通过

## 3. 状态库按时刻读最近的几个

- [x] 3.1 先写测试（design D6）：`tests/modules/statestore/test_repo.py` 加 `most_recent`：只看每个键的最新版本（旧版本满足条件、最新版本不满足的不给出）、按筛选列等于给定值筛、按时刻列从晚到早、最多 limit 个、时刻相同按键排序、筛选列给 None 时按为空筛、没有满足的给出空。验证：要开着 OrbStack（`open -a OrbStack`）；确认因为 `most_recent` 不存在而失败
- [x] 3.2 实现：`modules/statestore/repo.py` 的 `VersionedStore.most_recent`。验证：3.1 的测试通过；`tests/modules/statestore` 原有的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 4. 表和迁移

- [x] 4.1 先写测试（design D1、D11）：`tests/bootstrap/test_database.py` 的「空数据库升级到最新版本」加 `caserecords_cases`（版本表的列加 symbol、case_type、status、concluded、concluded_at、ended_at）、`caserecords_proposals`（版本表）、`caserecords_findings`（case_id、seq、symbol、data，主键 case_id 和 seq）的列和主键，「表名以写入它的模块开头」加三张表属于 `caserecords`；`tests/bootstrap/test_app.py` 的表的清单加三张表，最新迁移版本改为 `"0012"`。验证：要开着 OrbStack；确认新断言因为表不存在而失败
- [x] 4.2 实现：新建 `modules/caserecords/repo.py` 的三个表定义（design D1）；迁移 `bootstrap/migrations/versions/v0012_create_case_records.py`，写明每一列和主键、不 import 业务代码，`down_revision = "0011"`。验证：在「状态库快照」的 v0011 已经在 main 上以后跑 4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 记录的模型和写入规则

- [x] 5.1 先写测试（design D2、D5）：`tests/modules/caserecords/domain/test_record.py` 覆盖：`CaseRecord` 的状态（进行中、已结束、不再运行）和耗时；开始运行、不再运行、结论、结束各写一次，再写时原样返回（"现在"更晚也不变）；方案记录的审核和回应按 spec「重复写入和写入的顺序」的表交替（每一行一例）；Critic 不可用、风控结果再写不变；顺序不对时抛 `ValueError`（没有记录、不再运行的案件写结论和结束、不再运行和开始运行互相冲突、还没有审核就写回应）。验证：确认因为 `modules/caserecords/domain/record.py` 不存在而失败
- [x] 5.2 实现：`modules/caserecords/domain/record.py` 的枚举、dataclass 和写入规则（纯函数，"现在"作为参数），`domain/__init__.py` 的说明。验证：5.1 的测试通过；`tests/architecture` 通过（domain 只 import shared、contracts 和本模块）；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 存储

- [x] 6.1 先写测试（design D1、D5、D6、D11）：新建 `tests/fakes/case_records.py`（`CaseRecordStore` 的内存实现，读取的筛选和排序和 spec「读取案件记录」相同）；新建 `tests/modules/caserecords/test_repo.py`：`PostgresCaseRecordStore` 的往返（三种状态的记录、方案记录的审核、回应和风控结果、发现按写入的顺序）、`recent_ended` 和 `last_concluded` 的筛选和排序（`symbol=None` 的寻股）、`add_findings` 已经有发现时不写、数据库连不上和表不存在时报错、存着的记录读不出来时报错；`test_database.py` 的「空数据库升级到最新版本」补上案件表、方案表能保存和读取，发现表能追加和读取。每个测试的说明写明对应的场景。验证：要开着 OrbStack；确认因为 `PostgresCaseRecordStore` 不存在而失败
- [x] 6.2 实现：`modules/caserecords/ports.py` 的 `CaseRecordStore`；`repo.py` 的 `PostgresCaseRecordStore`（cases、proposals 各一个 `VersionedStore`，保存时从记录算出筛选列；findings 在一个事务里先看有没有、没有才插入）。验证：6.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 用例

- [x] 7.1 先写测试（design D3、D5、D6、D10）：新建 `tests/modules/caserecords/test_service.py`，用 6.1 的内存实现和测试时钟覆盖 case-records spec「案件记录的内容」「写入的时刻」「方案记录」「股票池增删的发现」「重复写入和写入的顺序」「读取案件记录」的全部场景；「不写程序日志」由 `CaseRecordService` 不收程序日志保证，不另写测试，在 8.1 的对应表里写明；`tests/modules/caserecords/test_repo.py` 用 `CaseRecordService` 和真的数据库跑「案件记录的存储」的三个场景。每个测试的说明写明对应的场景。验证：确认因为 `CaseRecordService` 不存在而失败
- [x] 7.2 实现：`modules/caserecords/service.py` 的 `CaseRecordService`（design D5、D6 的接口）；`__init__.py` 的说明（模块做什么、三张表、不写程序日志、写入方是「案件工作流」「排队运行」）和导出（`CaseRecordService`、`CaseRecordStore`、`PostgresCaseRecordStore`、记录的模型和枚举）。验证：7.1 的测试通过；`tests/architecture` 通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 8. 收尾

- [x] 8.1 对照本变更的两份 delta spec，逐条列出每个场景由哪个测试覆盖，没有遗漏；state-store「表的归属和迁移」原有的场景仍有测试。验证：对应表写在完成报告里
- [x] 8.2 归档前：「状态库快照」先归档、改过 state-store「表的归属和迁移」的，按当时的主 spec 重写本变更的 delta，只加本变更的三张表；确认 v0012 的 `down_revision` 是当时最新的版本、`test_app.py` 的最新版本是 `"0012"`。验证：`openspec validate add-case-records --strict` 通过；归档后的主 spec 里快照表还在
- [x] 8.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-case-records --strict`。验证：全部通过（要开着 OrbStack）；`git status` 里没有 `tests/` 下的文件被暂存
