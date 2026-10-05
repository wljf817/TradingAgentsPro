# Tasks

## 1. 准备

- [x] 1.1 确认这台机器的 Docker 能用。没有 Docker 时停下来，请人先装好 Docker Desktop 或 OrbStack 并启动（design D7）。验证：`docker info` 能显示服务器版本
- [x] 1.2 加依赖：`uv add sqlalchemy "psycopg[binary]" alembic`、`uv add --dev testcontainers`。mypy 报某个包没有类型标注时，在 `pyproject.toml` 里像 `exchange_calendars` 那样加 override，并写明原因。验证：`uv sync --locked`、`uv run mypy`、`uv run pytest` 通过，行为没有变化
- [x] 1.3 `tests/conftest.py` 增加数据库的 fixture（design D7）：
  - `postgres_url`：整个测试运行共用一个。第一次用到时，用 testcontainers 的 `PostgresContainer("postgres:18", driver="psycopg")` 启动容器，测试结束时删除
  - `empty_database`：每个测试一个。在容器里 `CREATE DATABASE` 一个新的空数据库，返回它的地址，测试结束后删除

  README 的「常用命令」写明：完整的测试要开着 Docker，不用数据库的测试不需要。验证：写一个临时测试，连上 `empty_database` 执行 `select 1`，通过后删掉

## 2. 状态库模块

- [x] 2.1 先写测试 `tests/modules/statestore/test_repo.py`。用测试专用的记录和表：一个 frozen dataclass，含 Decimal、带时区的时刻、可选字段、嵌套的 dataclass 和枚举；表用 `versioned_table` 定义，在 `empty_database` 里直接建。spec 场景里的"订单"换成这条测试记录，因为这些场景只关心版本，和记录是什么无关：
  - 「只追加的版本记录」五个场景：直接查表，核对版本号、写入时刻和内容。"同一个键同时保存"：一个连接开着事务插入版本 2；另一个线程保存同一个键，等它在锁上等待（查 `pg_stat_activity`）再提交事务，确认这次保存报错、版本 2 是先插入的内容
  - 「读取最新版本」：读到最新版本、没有这个键、表里没有记录时列出；按筛选列列出时只看最新版本，两个方向都测（旧版本满足、最新版本不满足；旧版本不满足、最新版本满足）；结果按键排序
  - 「记录原样读回」：测试记录的每一种字段
  - 「重启后读到同样的状态」：新建 Engine 和存储，再读、再保存
  - 「读写出错时报错」三个场景：连一个没有人监听的端口；空数据库里不建表；直接往表里插一条缺字段的记录，读取和列出都报错
  - `connect` 建的连接，会话时区是 UTC（`show timezone`）

  验证：运行这个文件，确认因为模块不存在而失败
- [x] 2.2 实现 `modules/statestore/`：`repo.py` 里的 `connect`、`versioned_table`、`VersionedStore`，`__init__.py` 导出它们（design D1、D2、D4、D5）。验证：2.1 的测试通过；`uv run pytest tests/architecture`、`uv run lint-imports`、`uv run mypy` 通过

## 3. 迁移

- [x] 3.1 先写测试 `tests/bootstrap/test_database.py`，用 `empty_database`，覆盖 spec「表的归属和迁移」的三个场景：
  - 空数据库升级到最新版本：有三张表和 `alembic_version`，三张表的列和 design D1 一致。"三张表都能保存和读取"由第 4 组的存储测试覆盖，它们都跑在升级过的数据库上
  - 已经是最新版本时再升级：升级以后直接往 `execution_orders` 插入 P1 的两个版本，再升级一次，表和行都不变
  - 表名以写入它的模块开头：列出 public 下除 `alembic_version` 以外的表，每个表名都以 `tradingagentspro.modules` 下的某个包名加下划线开头

  验证：确认因为 `migrate` 不存在而失败
- [x] 3.2 实现迁移和 `migrate`（design D6，三张表见 D1）：
  - `bootstrap/migrations/`：`__init__.py`、`env.py`、`versions/__init__.py`、`versions/v0001_create_trading_state.py`
  - `bootstrap/database.py`：`migrate`

  验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run pytest tests/architecture` 通过，迁移文件也在检查范围里
- [x] 3.3 `tests/conftest.py` 增加 `database` fixture：整个测试运行只升级一次，在 `postgres_url` 的默认数据库上；每个测试开始前清空 public 下除 `alembic_version` 以外的全部表；返回 `connect` 建的 Engine。README 写明怎样加一个迁移：在 `versions/` 里新建文件，`revision` 的编号加 1，`down_revision` 指向上一个。验证：写一个临时测试，先后两个测试都看到空表，通过后删掉

## 4. 三个存储

- [x] 4.1 `Protection.is_active`（design D8）：
  - 先写测试 `tests/modules/execution/domain/test_protection.py`，对应 spec「读取最新版本」里止损保护表那一行：止损单还没挂出去、挂着、状态未知时为真；止损单已经结束、没有止损单时为假；无保护仓位时为真，不论有没有止损单。确认失败
  - 再实现。`tests/fakes/protections.py` 和 `ProtectionStore.active` 的说明改用它；风控和执行的 `ports.py` 里三个存储接口的说明改为"内容有变化时写入新版本"，并指向 state-store spec

  验证：新测试和全部已有测试通过
- [x] 4.2 先写存储的测试，都用 `database` fixture：
  - `tests/modules/risk/test_repo.py`：预留表那一行的三个场景（释放的预留不再有效但仍能读到、全部成交的预留不再有效、部分成交的预留仍然有效），用 pytest 参数化，同时跑 `InMemoryReservationStore` 和数据库实现。"预留原样读回"只跑数据库实现
  - `tests/modules/execution/test_repo.py`：
    - 同时跑内存实现和数据库实现：订单表和止损保护表那两行的场景（结束的订单不再列为未结束、一只股票的订单、止损单结束后不再需要对齐、无保护仓位需要对齐、结束以后又挂新的止损单），以及"读到最新版本""没有这个键""表里没有记录时列出"
    - 只跑数据库实现："订单原样读回""止损保护原样读回"、「重启后读到同样的状态」
  - 列出的结果按集合比较，不比较顺序（design 的 Risks）

  验证：确认数据库实现的用例因为类不存在而失败，内存实现的用例通过
- [x] 4.3 实现两个模块的 `repo.py`，并从包根导出（design D1、D4）：
  - `modules/risk/repo.py`：`risk_reservations` 表、`PostgresReservationStore`
  - `modules/execution/repo.py`：`execution_orders`、`execution_protections` 表，`PostgresOrderStore`、`PostgresProtectionStore`

  表的定义和迁移 v0001 一致。验证：4.2 的测试通过；`uv run lint-imports`（模块内部分层、没有循环）、`uv run pytest tests/architecture`、`uv run mypy` 通过

## 5. 收尾

- [x] 5.1 人同意 proposal「需要人决定」第 1、2 条以后，修改完整版 Proposal 的「共享状态库」：
  - 读写规则的第一条改为：写入时带时间戳和版本号，案件里产生的条目还带案件 ID；只追加不覆盖
  - 三类内容的表里，"当前任务状态"一行补上订单和止损保护（执行模块）、额度预留（风控引擎）

  验证：只改了这两处，和 state-store spec 一致
- [x] 5.2 对照 spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 5.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`，全部通过
