# Proposal

## Why

M1 的风控和执行模块把预留、订单和止损保护交给"存储"接口保存，现在只有测试用的内存实现，进程一退出数据就没了。M2 要在模拟盘上真实运行，这三类数据必须存进 PostgreSQL，重启以后读回来继续用。

共享状态库以后还要放案件、任务、结果等更多数据。所以第一次接数据库时，先定下所有表共用的写法：Proposal 要求带时间戳和版本号、只追加不覆盖、每类字段只有一个写入者。

本变更的目的只有一个：**定下共享状态库在 PostgreSQL 里的统一写法（只追加的版本表、迁移、测试库），并用它实现 M1 已有的三个存储。**

引用的 Proposal（完整版）章节：

- 「共享状态库」：三类内容和各自的写入者；读写规则（写入带时间戳、版本号、案件 ID，只追加不覆盖；每类字段只有一个写入者；每个案件读一份快照）
- 「记录、复盘与经验」：四部分记录都存在 PostgreSQL 里；状态库保存当前状态
- 「技术栈与部署」：PostgreSQL 同时作为 DBOS 的状态存储；用 Docker 运行 PostgreSQL
- 「风控引擎与执行」的"额度预留"：进程重启后，根据状态库和券商对账结果重建预留
- 「订单生命周期」：每次状态变化都写入共享状态库

## What Changes

- 新增状态库模块 `modules/statestore/`，对应架构图的「共享状态库」节点，提供所有表共用的写法：
  - 连接数据库，会话时区固定为 UTC
  - 只追加的版本表：内容有变化时写入一个新版本，每个键的版本号从 1 开始递增，并记下写入时刻（取自 Clock）；内容和最新版本相同时不写；已有的版本不修改、不删除
  - 读取：按键读出最新版本；列出记录时只看每个键的最新版本，按最新版本筛选
  - 记录整份存成 JSON，读回的和保存时完全相同：Decimal 的每一位小数、时刻、枚举、嵌套的记录都不变
  - 数据库连不上、表不存在、记录读不出来时直接报错，不当作没有记录
- 用它实现三张表和三个存储，从各自模块的包根导出，由 M2 的「启动与装配」注入：
  - 风控模块的 `repo.py`：预留表 `risk_reservations`，实现 `ReservationStore`
  - 执行模块的 `repo.py`：订单表 `execution_orders`、止损保护表 `execution_protections`，实现 `OrderStore`、`ProtectionStore`
- 执行模块的 domain：`Protection` 增加 `is_active`，表示需要对齐。内存实现和数据库实现共用这个判断
- 迁移用 Alembic，脚本放在 `bootstrap/migrations/`，第一个迁移建出三张表。`bootstrap` 提供"升级到最新版本"的函数：现在给测试用，以后由「启动与装配」在启动时调用
- 测试连真实的 PostgreSQL 18：每次运行测试，由 testcontainers 启动一个临时容器，用完即删。三个存储共用一组测试，同时测内存实现和数据库实现
- 新增依赖 SQLAlchemy、psycopg、Alembic，开发依赖 testcontainers

## Non-goals

- 从环境变量读取数据库地址、接入 DBOS、启动时自动迁移：放在 M2「启动与装配」。DBOS 的系统表放在它自己的数据库里，由 DBOS 管理，不归本变更的迁移
- 交易记录、已结算资金、最近成交日：M2「交易记录」
- 案件快照：在 M4 实现。本变更只定下快照的做法（design D3），三张表不需要为它改动
- 程序日志、案件记录：M4
- 风控"一次只检查一个方案"的真正串行：M2「下单流程」
- 把持仓、现金、当日 P&L 写进状态库：等「交易记录」和「下单流程」定下首次检查的账户数据从哪来
- 健康状态和熔断状态仍放在内存里，这是 M1 定下的
- 在数据库层面禁止修改和删除（触发器、权限）、备份和清理旧数据、回滚迁移：见 design 的省略清单

## Capabilities

### New Capabilities

- `state-store`：只追加的版本记录、读取最新版本（含三张表各自怎样列出记录）、记录原样读回、重启后读到同样的状态、读写出错时报错、表的归属和迁移

### Modified Capabilities

无。三个存储的行为不变：risk-reservations「预留保存在存储里」、order-lifecycle「保存订单」、stop-protection「保存止损保护」已经要求重启后从存储读出，本变更给它们补上数据库实现。

## Impact

- **代码**：
  - 新增 `modules/statestore/`：`__init__.py`、`repo.py`
  - 新增 `modules/risk/repo.py`、`modules/execution/repo.py`，两个包根导出数据库实现
  - `modules/execution/domain/protection.py`：增加 `Protection.is_active`
  - 新增 `bootstrap/database.py`、`bootstrap/migrations/`
- **测试**：
  - `tests/conftest.py`：增加数据库的 fixture
  - 新增 `tests/modules/statestore/`、`tests/modules/risk/test_repo.py`、`tests/modules/execution/test_repo.py`、`tests/bootstrap/test_database.py`
  - `tests/fakes/protections.py` 改用 `Protection.is_active`
- **依赖**：`sqlalchemy`、`psycopg[binary]`、`alembic`；开发依赖 `testcontainers`
- **开发环境**：数据库测试需要 Docker。这台 Mac 现在没有 Docker，实现之前要先装好（Docker Desktop 或 OrbStack）。GitHub Actions 的运行环境自带 Docker，CI 不用改
- **文档**：README 写明需要 Docker，以及数据库测试和写迁移的方法
- **对后续变更的影响**：
  - M2「交易记录」：用同一套版本表
  - M2「启动与装配」：从环境变量读数据库地址，用 `statestore.connect` 建连接；启动时先把数据库升级到最新版本，再把三个存储注入各模块
  - M4 案件快照：复制成一份独立的快照记录，见 design D3

## 需要人决定

1. **交易侧的记录没有案件 ID**：「共享状态库」的读写规则要求"写入时带时间戳、版本号、案件 ID"。但订单、止损保护和预留都不属于某个案件：
   - 订单对应的是方案；紧急退出和本地备用止损的方案由代码生成，本来就没有案件
   - 对账、对齐止损单时的写入也不在案件里

   推荐：改成"写入时带时间戳和版本号；案件里产生的条目还带案件 ID"。交易侧的记录通过方案 ID 追溯到案件。
2. **三类内容的表里没有订单、止损保护和预留**：「共享状态库」的表列出了每类内容的写入者，但没有执行模块的订单和止损保护，也没有风控引擎的额度预留。

   推荐：在"当前任务状态"一行补上：订单和止损保护（执行模块）、额度预留（风控引擎）。
