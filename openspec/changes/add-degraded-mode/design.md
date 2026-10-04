# Design

## Context

现状（变更 1 到 8 之后）：

- 执行模块启动后要先完成一次对账才接受新订单；对账结果放在实例的内存里（`ExecutionService._last`，对账的 design D5）。断线以后不会重新要求对账
- 风控的检查和再校验把"单日亏损熔断是否已经触发"作为输入，熔断后拒绝开仓类方案；熔断状态由紧急退出模块给出
- 止损单的对齐和紧急退出都不看系统健康状态，降级模式下照常运行
- 交易日历有 `session_at`（所在的常规时段）和 `next_session_after`（开盘晚于某个时刻的第一个时段），交易日历的 design 把"恢复后的第一个交易日"留给本变更选

动机见 proposal.md 的 Why，要求见 specs/degraded-mode/spec.md。

### 模块边界

新增健康监控模块，不改其他模块，不新增也不修改跨模块传递的 Pydantic 模型。健康信号和状态是本模块的 frozen dataclass，由包根导出，给 M2 的工作流和下一个变更的风控输入用。

```
modules/health
  __init__.py       导出公开的名字
  domain/health.py  HealthCheck、HealthMode、HealthSignals、HealthStatus，
                    以及 abnormal_checks、recovery_day
  service.py        HealthService：check、reconciled、status
  config.py         HealthConfig：数据延迟上限、调度器心跳超时
```

没有 `ports.py` 和 `repo.py`：信号由调用方从适配器、Agent 网关和调度器拼好传入；状态放在内存里（D4）。

## Goals / Non-Goals

**Goals:**

- 判定全部由纯函数完成，service 只负责读当前时刻、找交易时段、保存状态
- 状态转换只有一张表，每一行都有场景和测试；性质测试拿随机的判定和对账序列和这张表对照

**Non-Goals:**

按「编程风格」省略的情况：

- 健康信号本身不合理，比如心跳时刻晚于当前时刻：不检查
- 判定和对账完成交错，对账完成时检查其实已经又异常了：先回到正常，下一次判定再进入降级
- 只记最近一次判定异常的检查，不记历史：历史在程序日志里（M2）
- 单只股票没有行情：不算数据延迟，停牌是正常情况；紧急退出对没有行情的股票本来就不判定个股跌幅

## Decisions

### D1 信号作为一次判定的输入

```python
@dataclass(frozen=True, slots=True)
class HealthSignals:
    """一次判定用的健康信号，由调用方从各个来源拼好。"""

    pm_model_available: bool  # PM 还有可用的模型
    critic_model_available: bool  # Critic 还有可用的模型
    broker_connected: bool
    last_quote_at: datetime | None  # 行情最近一次更新的时刻；从来没有更新过时为 None
    reserve_budget_exhausted: bool  # 持仓保留预算用完
    scheduler_heartbeat_at: datetime  # 调度器最近一次心跳的时刻
```

- 和风控读取账户数据、紧急退出读取持仓一样，模块只做判断，不读取外部数据
- 调度器心跳不能为空：调度器启动时先写一次心跳，再开始判定，由 M2、M3 接线时保证
- 模型可用、预算、心跳的来源到 M3 才有，M2 按正常传入

考虑过：每个来源各自上报（`report_broker(connected)` 之类），健康监控记下最新的值

- 没有采用：要多存五份状态，还要处理"某个来源一直没上报"；拼成一次快照更简单，也更好测

### D2 五项检查

```python
def abnormal_checks(
    signals: HealthSignals, now: datetime, session: TradingSession | None, config: HealthConfig
) -> frozenset[HealthCheck]:
    """这次判定异常的检查。session 是当前时刻所在的常规时段，盘外为 None。"""
```

- 模型 API：`not (pm_model_available and critic_model_available)`
- 数据延迟：`session` 不为空，并且 `now - max(last_quote_at, session.open) > max_quote_delay`；`last_quote_at` 为空时按开盘时刻算
  - 从开盘起算：开盘前的行情是昨天收盘或盘前的，不能一开盘就算延迟
  - 盘外不检查：盘前盘后行情更新稀疏，而且盘外不下单
- 调度器心跳：`now - scheduler_heartbeat_at > scheduler_heartbeat_timeout`，任何时刻都检查
- 两个超时都是"超过"才算异常，等于时正常

### D3 三种状态

```python
class HealthMode(StrEnum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"  # 检查都恢复了，等一次对账


@dataclass(frozen=True, slots=True)
class HealthStatus:
    mode: HealthMode = HealthMode.NORMAL
    abnormal: frozenset[HealthCheck] = frozenset()  # 最近一次判定异常的检查
    degraded_since: datetime | None = None  # 进入降级模式的时刻；正常时为 None

    @property
    def degraded(self) -> bool:
        """处于降级模式：降级或等待对账。"""
        return self.mode is not HealthMode.NORMAL

    def judged(self, abnormal: frozenset[HealthCheck], now: datetime) -> Self:
        """一次判定之后的状态，见 spec「进入和退出降级模式」的表。"""
        if abnormal:
            return replace(
                self,
                mode=HealthMode.DEGRADED,
                abnormal=abnormal,
                degraded_since=self.degraded_since or now,
            )
        if self.mode is HealthMode.NORMAL:
            return self
        return replace(self, mode=HealthMode.AWAITING_RECONCILIATION, abnormal=abnormal)

    def reconciled(self) -> Self:
        """对账完成之后的状态：只有等待对账时回到正常。"""
        if self.mode is HealthMode.AWAITING_RECONCILIATION:
            return type(self)()
        return self
```

- 等待对账单独成一个状态：满足"恢复后先对账"。降级期间完成的对账不算，因为那时还没恢复
- 不管因为哪一项进入降级，退出前都要对账（proposal「需要人决定」第 3 条）。模型 API 恢复时对账也不贵
- 对账有没有不一致都算完成：不一致由执行模块自己暂停开仓，直到之后的对账没有不一致（对账的 spec）
- 进入时刻从进入降级起保持不变，经过等待对账再回到降级也不变：对外看是同一次降级

考虑过：只有券商断线恢复后才要对账

- 没有采用：要分别记每一项是什么时候恢复的；统一要求对账，规则只有一条

### D4 状态放在内存里

```python
class HealthService:
    def __init__(self, clock: Clock, calendar: TradingCalendar, config: HealthConfig) -> None:
        self._clock, self._calendar, self._config = clock, calendar, config
        self._status = HealthStatus()

    def check(self, signals: HealthSignals) -> HealthStatus:
        now = self._clock.now()
        abnormal = abnormal_checks(signals, now, self._calendar.session_at(now), self._config)
        self._status = self._status.judged(abnormal, now)
        return self._status

    def reconciled(self) -> HealthStatus:
        self._status = self._status.reconciled()
        return self._status

    @property
    def status(self) -> HealthStatus:
        return self._status
```

- 和执行模块的对账状态一样不存储：重启以后执行模块本来就要先对账才接受新订单；只要有一项还异常，下一次判定就回到降级
- 重启后到第一次判定之间是正常。M2 的 bootstrap 启动时先判定一次，再接受新订单
- 同一个实例由调用方串行调用，和其他模块一样

考虑过：存进存储，重启后沿用

- 没有采用：沿用的状态在重启时多半已经过时，还是要重新判定；要多一个端口和一张表

### D5 恢复后的第一个交易日

```python
def recovery_day(moment: datetime, calendar: TradingCalendar) -> date:
    """收盘时刻晚于 moment 的第一个交易日。"""
    session = calendar.session_at(moment) or calendar.next_session_after(moment)
    return session.day
```

- 盘中：`session_at` 给出当天。盘前、收盘时刻或之后、休市日：`session_at` 为空，`next_session_after` 给出开盘晚于这个时刻的第一个时段，也就是下一个收盘的交易日
- 选"第一个收盘的交易日"而不是一律下一个交易日：盘中恢复时，当天收盘的定时心跳就能补上（proposal「需要人决定」第 1 条）
- 只有 M3 的定时心跳用它，所以只导出纯函数，service 不包一层

### D6 包根导出

`HealthCheck`、`HealthConfig`、`HealthMode`、`HealthService`、`HealthSignals`、`HealthStatus`、`abnormal_checks`、`recovery_day`。纯函数导出是为了测试只从包根 import，`recovery_day` 也给 M3 用。

### D7 测试

```
tests/modules/health/builders.py           基准数据：纽交所样例日历、2026-10-14 15:00Z、基准信号；
                                           新建 HealthService 的辅助函数
tests/modules/health/domain/test_health.py 五项检查表的每一行和边界；状态转换表的每一行；
                                           recovery_day 表的每一行；状态机性质测试
tests/modules/health/test_service.py       spec 的场景
tests/modules/health/test_config.py        spec「健康配置」
```

- 状态机性质测试（Hypothesis 的 `RuleBasedStateMachine`）：随机交错"判定，有或没有异常的检查"、"对账完成"和拨动时间，用一个按表写的参考模型对照：
  - 状态和参考模型一致
  - 有异常的检查时一定是降级
  - 只有"等待对账时对账完成"能回到正常
  - 处于降级模式时进入时刻不为空、不会变；正常时为空
- 每个 spec 场景至少对应一个测试，测试名里写明场景

## Risks / Trade-offs

- [数据延迟只看全部行情的最近更新时刻，看不出单只股票没有行情] → 单只股票停牌是正常情况，不该降级；紧急退出对没有行情的股票不判定个股跌幅
- [调度器心跳超时 300 秒，卡住的调度器要 5 分钟才发现] → 降级只暂停开仓，紧急退出和止损单不依赖调度器；超时可以配置
- [券商连接时断时续，降级模式反复进出] → 每次退出都要对账，对账不贵；告警由 M2 按状态变化发
- [重启后到第一次判定之间是正常] → bootstrap 启动时先判定再接受新订单；执行模块启动后也要先对账

## Migration Plan

只新增一个模块，不需要迁移；需要回滚时直接 revert。
