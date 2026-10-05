# Design

## Context

现状（M1 之后）：

- 三个存储接口：风控的 `ReservationStore`（`get`、`active`、`save`），执行模块的 `OrderStore`（`get`、`open_orders`、`of_symbol`、`save`）和 `ProtectionStore`（`get`、`active`、`save`）
  - 接口说明里已经写好：数据库实现只追加不覆盖，读取时取最新版本
  - 现在只有 `tests/fakes/` 里的内存实现，按键覆盖
- 谁在什么时候保存：
  - 执行模块只在订单或止损保护有变化时保存
  - 风控的"上报成交"和"释放"每次调用都保存，即使预留没变。M2 的下单工作流每跟进一次订单就上报一次累计成交
- 数据量：每天几个到几十个方案，每笔订单、每张止损单一天变化几次。几年下来每张表在十万行以内
- 运行方式：单进程。M2 起 DBOS 在多个线程里跑工作流，存储会被多个线程同时调用。同一个键由调用方串行，这是 M1 的约定：同一笔订单一次只有一个调用在处理；风控一次只处理一个方案，真正的串行在「下单流程」里做
- DBOS 3.x 依赖 SQLAlchemy 2.0.43 以上和 psycopg 3，它的系统表放在自己的系统数据库里
- 这台 Mac 现在没有 Docker，也没有 PostgreSQL；Proposal 的部署方式是用 Docker 运行 PostgreSQL
- 测试进程固定在 UTC+14 时区运行

动机见 proposal.md 的 Why，要求见 specs/state-store/spec.md。

### 模块边界

```
modules/statestore       新增，对应架构图的「共享状态库」节点
  __init__.py            导出 connect、versioned_table、VersionedStore
  repo.py                连接数据库；版本表的建表函数和读写
modules/risk
  repo.py                新增：risk_reservations 表、PostgresReservationStore
  ports.py               ReservationStore 的说明改为"内容有变化时写入新版本"
  __init__.py            导出 PostgresReservationStore
modules/execution
  repo.py                新增：execution_orders、execution_protections 表、
                         PostgresOrderStore、PostgresProtectionStore
  domain/protection.py   Protection 增加 is_active
  ports.py               OrderStore、ProtectionStore 的说明改为"内容有变化时写入新版本"
  __init__.py            导出两个数据库实现
bootstrap
  database.py            新增：migrate，把数据库升级到最新版本
  migrations/            新增：Alembic 的 env.py 和 versions/
```

- 依赖方向：风控和执行的 `repo.py` 只 import 状态库模块的包根；状态库模块不 import 任何其他模块，所以没有循环
- 不新增、也不修改跨模块传递的 Pydantic 模型。`TradePlan`、`RiskDecision` 作为预留的一部分，原样存进 JSON
- 本变更也改动了风控和执行两个模块，但只加 `repo.py`，service 和业务规则都不动。架构规则要求表和它的数据库实现放在拥有这张表的模块里，所以这些 `repo.py` 属于状态库这个节点在各模块里的部分。domain 只加一个判断（D8）

## Goals / Non-Goals

**Goals:**

- 一套写法覆盖以后所有的状态表：新增一类记录，只需定义一张表、写一个很薄的存储类、加一个迁移
- 数据库实现和内存实现的行为一致：两者跑同一组测试，用内存实现的 service 测试才有意义
- 不悄悄丢数据：数据库的错误不会被当成"没有记录"

**Non-Goals:**

按「编程风格」省略的情况：

- 在数据库层面禁止修改和删除（触发器、收回权限）：只有 repo 写表，而 repo 只插入
- 清理和归档旧版本：每天几十个版本，几年也只有十万行级别
- 备份：部署时再定
- 回滚迁移（downgrade）：迁移只写升级
- 数据库调用的超时：数据库和主程序在同一台机器上
- 数据库重启后连接池里的连接失效：重启后第一次用到这个连接的调用报错，下一次用新连接
- 列出记录时分页：数据量小
- 读取历史版本的接口：排查时直接查表
- 迁移的命令行入口和自动生成迁移：需要时再加
- 同一个键同时保存冲突时自动重试：调用方本来就按键串行，冲突说明哪里有错，直接报错更好

## Decisions

### D1 每类记录一张只追加的版本表，记录整份存成 JSONB

每张表的列都一样，只有键和筛选列不同：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| 键 | uuid 或 text | 方案 ID，或者股票 |
| `version` | integer | 版本号，每个键从 1 开始 |
| `recorded_at` | timestamptz | 写入时刻，取自 Clock |
| `data` | jsonb | 整份记录 |
| 筛选列 | 按需要 | 列出记录时用的条件，保存时从记录算出 |

主键是（键，`version`），它同时是按键读取用的索引。三张表：

| 表 | 键 | 筛选列 | 记录 |
| --- | --- | --- | --- |
| `risk_reservations` | `plan_id` | `active`：`Reservation.is_active` | `Reservation` |
| `execution_orders` | `plan_id` | `symbol`；`active`：订单不是终态 | `Order` |
| `execution_protections` | `symbol` | `active`：`Protection.is_active`（D8） | `Protection` |

表名是"模块名_内容"，一眼能看出归哪个模块，也就是谁是唯一的写入者。

记录整份存成 JSONB，而不是每个字段一列：

- M1 里 domain 的记录一直在加字段（比如订单的撤单要求）。整份存 JSON，加一个有默认值的字段不用迁移
- 只追加的表里，旧版本保留写入时的样子，不用为了新列回填旧版本
- 查询只有按键读取和按少数条件列出，用不到按字段的复杂查询。条件做成筛选列，保存时从记录算出，不用在 SQL 里解析 JSON

考虑过：

- 每个字段一列：数据库能约束类型，用 SQL 分析也方便。但每改一次字段都要写迁移、改映射代码。要用 SQL 分析的数据，比如交易记录和案件记录，到时候按需要设计成列
- 原地更新一行，另外存一张历史表：违反"只追加不覆盖"
- 全部记录放进一张通用表：表不再属于某个模块，"每类字段只有一个写入者"就没法从表上看出来

### D2 保存：内容没变就不写，版本号由数据库主键兜住

`save` 在一个事务里做两件事：先读这个键的最新版本，内容和要保存的相同就什么都不做；不同就写入版本号加 1 的新版本（第一次保存时版本号是 1）。

- 内容没变不写，是为了风控的"上报成交"和"释放"：它们每次都保存，下单工作流每跟进一次订单就上报一次。不这样做，一笔挂一整天的订单会留下几千个一样的版本。这条规则放在存储里，而不是改每个调用方：所有表一条规则，DBOS 的 step 重跑时重复保存也不会多出版本
- 比较的是 JSON 内容（D4），所以和 domain 记录的相等判断一致
- 同一个键的两次保存同时进行时，两边都读到同一个最新版本，都要写入同一个版本号，后完成的那次违反主键而报错，先写入的版本不受影响。这就是 spec 要求的行为，不用另写代码

考虑过：

- 所有表共用一个递增序号，代替每个键各自的版本号：序号在插入时分配，提交的先后可能和序号相反，用它排先后不可靠；要它也只是为了快照，而快照不按序号做（D3）
- 调用方传入"我读到的版本号"（乐观锁）：接口要跟着改；调用方本来就按键串行，主键已经能抓住真正同时发生的保存
- 改风控，只在预留有变化时保存：存储还是要能承受重复保存，在存储里做一次就够

### D3 案件快照不按版本回查，M4 复制成一份独立的记录

Proposal 要求每个案件读一份固定的快照，M6 还要用存下的快照重跑历史案件。两种做法：

- 按版本回查：快照只记一个时间点，读取时取每个键在那个时间点之前的最新版本
  - 时间点用什么都不可靠：插入时分配的序号，提交先后可能和序号相反，快照建好以后还可能冒出更早的序号；Clock 的时间可能回退
  - 每张表、每个读取都要支持按时间点查询，而且旧版本永远不能清理
- 复制：案件开始时，把这个案件要用的事实整理成一份快照记录，带快照 ID；本案件的 agent 只读这份记录，回放也读这份记录

选择复制，M4 实现。所以本变更的表不用为快照做任何准备：没有全局序号，也不按写入时刻查询。`recorded_at` 只用来排查问题。

### D4 公共写法放进新的状态库模块，记录用 pydantic 的 TypeAdapter 转成 JSON

三张表的读写方式完全一样（D1、D2），以后的状态表也一样，所以公共部分只写一份：

- 不能放 `shared`：纯区域，不能用 SQLAlchemy
- 不能放 `integrations`：模块不能 import 它
- 每个 `repo.py` 各抄一份：现在就有三份，以后更多，版本号和"内容没变不写"这样的细节容易抄错
- 新建状态库模块：它就是架构图里的「共享状态库」节点。其他模块的 `repo.py` 只用它的包根

接口大致是：

```python
def connect(url: str) -> Engine:
    """连接数据库，会话时区固定为 UTC。"""


def versioned_table(name: str, key: Column[Any], *columns: Column[Any]) -> Table:
    """只追加的版本表：键、版本号、写入时刻、记录内容，再加上列出时用的筛选列。"""


class VersionedStore[T]:
    """按键保存记录的各个版本，读取最新版本。T 是 domain 的 frozen dataclass。"""

    def __init__(self, engine: Engine, clock: Clock, table: Table, record: type[T]) -> None: ...

    def save(self, key: object, record: T, **columns: object) -> None:
        """内容和最新版本不同时写入新版本；columns 是这个版本的筛选列的值。"""

    def get(self, key: object) -> T | None:
        """这个键的最新版本；没有时为 None。"""

    def latest(self, **where: object) -> tuple[T, ...]:
        """每个键的最新版本里，筛选列等于给定值的那些，按键排序。"""
```

每个模块的存储类只做转接，比如订单：

```python
ORDERS = versioned_table(
    "execution_orders",
    Column("plan_id", Uuid, nullable=False),
    Column("symbol", String, nullable=False),
    Column("active", Boolean, nullable=False),
)


class PostgresOrderStore:
    """满足 OrderStore：订单存在状态库的 execution_orders 表里。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, ORDERS, Order)

    def get(self, plan_id: UUID) -> Order | None:
        return self._versions.get(plan_id)

    def open_orders(self) -> tuple[Order, ...]:
        return self._versions.latest(active=True)

    def of_symbol(self, symbol: str) -> tuple[Order, ...]:
        return self._versions.latest(symbol=symbol)

    def save(self, order: Order) -> None:
        self._versions.save(
            order.plan_id, order, symbol=order.symbol, active=not order.status.is_terminal
        )
```

- `latest` 先按键取最新版本，再按筛选列过滤，所以旧版本满足条件、最新版本不满足的键不会列出来（spec「读取最新版本」）
- 每张表各用一个 SQLAlchemy `MetaData`，表之间没有外键，不需要共用

记录和 JSON 之间的转换用 pydantic 的 `TypeAdapter`：

- 写入：`dump_python(record, mode="json")`。Decimal 转成字符串，每一位都保留；时刻转成带时差的 ISO 8601 字符串；UUID 和枚举转成字符串；tuple 转成列表
- 读取：`validate_python(data)` 按 dataclass 的类型标注重建记录，嵌套的 Pydantic 模型和 dataclass 一起重建
- 2026-10-05 在临时目录里验证过：`Reservation`、`Order`、`Protection` 都能原样读回，包括 `Decimal("50.0066666667")` 和带时区的时刻
- 读不出来时（缺字段、类型不对），pydantic 报错，原样抛出（spec「读写出错时报错」）

记录的格式以后怎么改：

- 加一个有默认值的字段：不用迁移，旧版本读出来用默认值
- 改名、删除字段、改类型：在同一个变更里写一个数据迁移，改写旧版本的 JSON。这只改存储的格式，不改记录的事实，不算违反"只追加不覆盖"

考虑过：

- 每类记录手写 `to_dict`、`from_dict`：代码多，加字段时容易漏
- pickle：不安全，看不懂，还和 Python 版本绑定

### D5 SQLAlchemy Core 加 psycopg 3，同步调用

- 同步：M1 的 service 都是同步的，DBOS 支持同步的工作流
- SQLAlchemy Core，不用 ORM：记录整份存成 JSON，不需要把对象映射到列
- 驱动用 psycopg 3：DBOS 本来就依赖 SQLAlchemy 和 psycopg 3，不多一个驱动
- `connect` 建 Engine 时把会话时区设为 UTC。时刻本身带时区，不受会话时区影响；但以后在 SQL 里按日期计算（比如当天的成交）会受影响，所以统一定成 UTC
- 每次 `save` 一个事务；读取各用一个连接。Engine 的连接池可以被多个线程同时使用
- 数据库的错误（连不上、表不存在、主键冲突）都是 SQLAlchemy 的异常，原样抛出，不包装：调用方现在不区分它们，DBOS 的 step 失败了会重跑

考虑过：

- 直接用 psycopg，不用 SQLAlchemy：Alembic 本来就依赖 SQLAlchemy，用它的 Table 定义表也更清楚
- 异步：M1 的 service 是同步的，改成异步要改全部调用方

### D6 迁移：Alembic，手写，放在 bootstrap 里，只升级

- `bootstrap/migrations/`：`env.py` 和 `versions/`。`env.py` 只用调用方传进来的连接，不读环境变量，也不 import 业务代码
- 第一个迁移 `versions/v0001_create_trading_state.py`：用 `op.create_table` 写明三张表的每一列。迁移不 import 业务代码，以后业务代码再改，旧迁移也不会跟着变
- `bootstrap/database.py` 的 `migrate(engine)`：在一个事务里升级到最新版本。PostgreSQL 的建表语句也在事务里，中途失败时数据库保持原样。现在给测试用，以后由「启动与装配」在启动时调用
- 迁移文件名以字母开头，`revision` 用递增的编号（`"0001"`）。目录里放 `__init__.py`，它们就是普通的模块，ruff、mypy 和架构测试都会检查。Alembic 会跳过 `__init__.py`
- `downgrade` 直接抛错：回滚靠修代码、写新的迁移，或者恢复备份

放在 bootstrap 里：模块的目录结构是固定的，放不下 `migrations/`；状态库模块也不该知道别的模块有哪些表。bootstrap 负责启动，启动时升级数据库正好是它的事。

考虑过：

- 启动时 `create_all`：只能建新表，改不了已有的表。数据要跨部署保留，第一张表就需要迁移
- 自动生成迁移：要先把所有模块的表收集到一起，只能靠 import 的副作用；三张表结构一样、都很小，手写也很短。数据库实现的测试都跑在升级后的数据库上，迁移和代码不一致（比如少一列、类型不对）测试就会失败
- 自己写一个按编号执行 SQL 文件的迁移工具：Alembic 是现成的标准做法

### D7 测试：testcontainers 启动 PostgreSQL 18

- `tests/conftest.py` 增加数据库的 fixture：
  - 第一次用到数据库的测试启动一个 `postgres:18` 容器，整个测试运行共用，用完即删。不用数据库的测试不需要 Docker
  - 容器启动后升级一次数据库；每个用到数据库的测试开始前，清空除迁移版本表以外的全部表
  - 迁移的测试另外用新建的空数据库（同一个容器里 `CREATE DATABASE`），看到的就是迁移建出来的样子
- 版本选 18：2026-10 时 PostgreSQL 当前的稳定大版本，部署也用它
- 每个存储的测试同时跑内存实现和数据库实现（pytest 参数化），覆盖 spec「读取最新版本」的那张表。"原样读回"、"版本号"这些只有数据库实现才有的行为，只测数据库实现
- 好处：每次测试都是干净的临时数据库，测试再怎么清表，也碰不到正式的数据库；本地和 CI 用同一种方式
- 代价：跑完整测试和提交前检查都要开着 Docker。部署机本来就用 Docker 运行 PostgreSQL

考虑过：

- 连一个已经在跑的 PostgreSQL（地址放环境变量）：要先手动启动，测试还可能误连正式数据库
- pytest-postgresql 用本机的 PostgreSQL 程序启动临时实例：本机和 CI 都要另外装 PostgreSQL
- pgserver 这类把 PostgreSQL 打包进 pip 的库：最近一次发布是 2024 年，没人维护

### D8 Protection.is_active

内存实现里判断"需要对齐"的条件写在 `InMemoryProtectionStore.active` 里，数据库实现保存时也要算同一个条件。把它移到 domain 的 `Protection.is_active`，两边共用，和 `Reservation.is_active` 一样：

- 当前止损单还没结束（还没挂出去、挂着、状态未知），或者
- 是无保护仓位

## Risks / Trade-offs

- [跑完整测试和提交前检查都要开着 Docker] → 部署机本来就用 Docker 运行 PostgreSQL；不用数据库的测试不受影响
- [JSON 里的字段名和 domain 绑在一起，字段改名后旧版本读不出来] → 改名、删除字段、改类型时，在同一个变更里写数据迁移；读不出来时报错（spec），不会悄悄丢数据
- [每次列出记录都要扫一遍全部版本] → 数据量小；以后真的变慢，再加只覆盖最新版本的索引或者改写查询，接口不变
- [每次运行测试都要启动容器，多几秒] → 只在第一次用到数据库时启动，整个测试运行共用一个
- [内存实现和数据库实现列出记录的顺序不同：内存按第一次保存的先后，数据库按键] → 接口不保证顺序，测试按集合比较

## Migration Plan

- 现在没有任何已存的数据，第一次部署时由迁移建表（「启动与装配」）
- 回滚：revert 代码即可。数据库里多出来的表，旧代码不会用到
