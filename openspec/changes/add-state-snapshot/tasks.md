# Tasks

## 1. 前提、Proposal 和注释

- [x] 1.1 确认前提：最新迁移是 v0010（`add-stock-screen` 已经归档），「案件记录」还没有 apply；按人对 proposal「需要人决定」的答复改 proposal（写成已定）和相关的 spec、design。验证：`ls src/tradingagentspro/bootstrap/migrations/versions/` 的最后一个是 v0010；`openspec validate add-state-snapshot --strict` 通过，proposal、spec、design、tasks 的说法一致
- [x] 1.2 改完整版 Proposal「共享状态库」读写规则的第三条，用 proposal「What Changes」里的措辞；`contracts/signal.py` 的 `EntryId` 注释改为"状态库条目的 ID：快照里的条目见 contracts.snapshot 的 entry_id"，类型不变。验证：`grep -n "条目 ID 带快照的版本" "docs/AI Agent 美股交易系统需求 Proposal.md"` 只有这一处；简版没有改动；`grep -n "M4 再定" src/tradingagentspro/contracts/signal.py` 没有结果

## 2. 快照的格式（contracts）

- [x] 2.1 先写测试 `tests/contracts/test_snapshot.py`（design D11）：
  - `entry_id`：九种类别、没有键和有键、键里有冒号和横线
  - `entry_version`：`s1/account`、`s12/quote/BBB` 给出版本；`E-1`、`quote/BBB`、`s0/account`、`sx/account`、`s1` 为 None
  - `check_reason`：spec「新的版本」的"原因不对"
  - `Snapshot`：`entries()` 的先后（spec「条目和条目 ID」的三个场景，按条目构造）、`entry()` 找得到和找不到、条目 ID 重复或前缀和版本不符时报错、`ScreenCandidate.score`
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.contracts.snapshot` 不存在而失败
- [x] 2.2 实现 `src/tradingagentspro/contracts/snapshot.py`（design D2、D4、D5）。验证：2.1 的测试通过；`tests/contracts/test_all_models.py` 通过（新模型都是 frozen、extra="forbid"）；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过（纯区域只用白名单里的 `re`）

## 3. 状态库模块：快照的存储和用例

- [x] 3.1 先写测试：
  - `tests/fakes/snapshots.py`：`InMemorySnapshotStore`（同一版 `add` 两次报错）
  - `tests/modules/statestore/test_service.py`：spec「读取快照和查条目」的两个场景；program-log delta「调度器的动作」里生成快照、寻股和新的版本、写条目出错时快照已经保存三个场景（对象、案件 ID、输出的引用、说明）；同一版保存两次报错、不写程序日志。用内存存储、`InMemoryProgramLogStore` 和按条目构造的快照
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `SnapshotService` 等不存在而失败
- [x] 3.2 实现：`modules/programlog/domain/entry.py` 加 `Action.TAKE_SNAPSHOT`；`modules/statestore/ports.py`（`SnapshotStore`）、`service.py`（`SnapshotService`，design D6、D10）；`__init__.py` 导出它们，说明写上快照（表在 3.4 写）。验证：3.1 的测试和 `tests/modules/programlog/` 原有的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（状态库只依赖 contracts、shared 和 programlog 的包根）
- [x] 3.3 先写测试（要开着 OrbStack：`open -a OrbStack`）：
  - `tests/modules/statestore/test_snapshot_repo.py`：`PostgresSnapshotStore` 跑 spec「快照的存储」的五个场景，用按条目构造的快照（各种条目都有，含 None 的可选项、多位小数、HTML 正文）
  - `tests/bootstrap/test_database.py`：「空数据库升级到最新版本」加 `statestore_snapshots` 的列和主键，并用 `PostgresSnapshotStore` 追加和读取；「表名以写入它的模块开头」加 `statestore_snapshots` 属于 `statestore`
  - `tests/bootstrap/test_app.py`：表的清单加 `statestore_snapshots`，最新迁移版本改为 `"0011"`

  验证：确认新断言因为表和 `PostgresSnapshotStore` 不存在而失败
- [x] 3.4 实现 `modules/statestore/repo.py` 的 `SNAPSHOTS` 和 `PostgresSnapshotStore`（design D6），`__init__.py` 导出它并在说明里写明表；迁移 `bootstrap/migrations/versions/v0011_create_snapshots.py`（`down_revision = "0010"`，design 的 Migration Plan）。验证：3.3 的测试和 `tests/modules/statestore/test_repo.py` 原有的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. 生成快照的 workflow

- [x] 4.1 先写测试：
  - `tests/workflows/snapshot_builders.py`：state-snapshot spec 的基准数据，用各模块的真用例加 `tests/fakes/` 的替身装好一个 `SnapshotFlow`（券商、行情源、股票池存储、新闻和公告的存储、财报的存储、粗筛的三个数据源和存储、`InMemorySnapshotStore`、程序日志），函数按需覆盖账户、行情、新闻和时钟；记下各数据源和存储收到的请求
  - `tests/workflows/test_snapshot_flow.py`：spec「每种案件的快照」「条目和条目 ID」「共享事实」「案件股票的事实」「新的版本」「同一版再生成」「数据读不到时」「快照配置」的全部场景（"默认配置"按测试约定不写）；每种条目的转换逐项核对（design D11）
  - 寻股：用 stock-screen 的场景配置和基准数据（`tests/modules/screener/baseline.py`），断言粗筛收到的排除股票是股票池给出的、`s1/screen` 和 `ScreenerService.screen` 返回的结果逐项相同、名单为空时也有这个条目；粗筛的数据源不可用时报错不保存
  - 阈值从配置读取，不在测试里硬编码；每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.workflows.snapshot_flow` 不存在而失败
- [x] 4.2 实现 `src/tradingagentspro/workflows/snapshot_flow.py`（`SnapshotConfig`、`SnapshotFlow`、`lookback_start`，design D7、D8、D9）。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 收尾

- [x] 5.1 对照三份 delta spec，逐条列出每个场景由哪个测试覆盖，没有遗漏（「快照配置」的默认值按测试约定不写测试，对照 `SnapshotConfig` 核对）。验证：对应表写在完成报告里
- [x] 5.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-state-snapshot --strict`。验证：全部通过（要开着 OrbStack）；`git status` 里没有 `tests/` 下的文件被暂存
- [ ] 5.3 归档前确认：本变更先于「案件记录」归档；v0011 的 `down_revision` 是当时最新的版本、`test_app.py` 的最新版本是 `"0011"`。验证：`openspec validate add-state-snapshot --strict` 通过；归档后的 state-store、program-log 主 spec 里原有的表、动作和场景都还在
