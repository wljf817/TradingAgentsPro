# Design

## Context

现状（M2 进行中，M3 完成了股票池、案件类型与结论、案件队列、案件阶段、复查日与催化剂）：

- 定时心跳模块 `modules/heartbeat/` 只有检查论点日期的纯逻辑（`domain/dates.py`）和 `HeartbeatConfig`（六个复查日与催化剂的配置项），没有 service、ports 和存储。review-dates spec 保证复查日和催化剂截止日期都是交易日，续期时新的复查日在今天之后至少 3 个交易日、到期的催化剂必须换新的
- 调度器的 `HeartbeatTrigger(case_type, symbol)` 在 `modules/scheduler/domain/case_type.py`：只接受七种心跳案件类型，针对一个状态的带股票、寻股和每周复盘不带，不符合时抛 `ValueError`。队列按优先级、触发时刻、入队的先后启动，降级期间不启动、照常排队；排队的心跳案件在开始时股票已经不在那个状态就不再运行
- 案件状态给出结束原因和有没有结论：系统故障（结论以前）、紧急退出中止（唤醒 PM 时）、结论无效都没有结论
- 股票池记录有 `score`（最新评分，可以为空）和 `expires_at`（只有候选有）；`expire_candidates` 移出到期的候选，由调用方每个交易日收盘后调用
- 交易日历有 `is_session`、`add_sessions`（之后第 n 个交易日，不含当天）、`next_session`、`last_session_before`（最近一个已收盘的时段）和 `session_closing_after`（时刻归属的交易日）
- 健康监控有 `recovery_day`（恢复后的第一个交易日），本来留给定时心跳顺延用；本变更不用它（proposal 第 3 条）
- M3 只写纯逻辑和测试：不接外部系统，不建表；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/close-check/spec.md。

### 模块边界

```
contracts（不改）
  case.py              CaseType  <- 检查项和在排队或运行的心跳案件的类型

shared（不改）
  trading_calendar.py  TradingCalendar：is_session、add_sessions、next_session

modules/heartbeat
  domain/close_check.py  CheckItem、ThesisDates、PoolCandidate、CloseSnapshot、close_check（新增）
  config.py              HeartbeatConfig 增加五个配置项
  __init__.py            增加导出
```

本次只改定时心跳一个模块，contracts、shared 和其他模块不改。跨模块传递的模型：

- 传入：`CaseType`（contracts 的 StrEnum）；其余是 `date`、`datetime`、`int`、`bool` 和交易日历。M4 从股票池的 `PoolEntry`（`score`、`expires_at`）、论点（`InvestmentThesis`、`WatchThesis` 的 `review_day` 和 `catalyst.deadline`）、调度器的 `QueuedCase`、`RunningCase` 和案件记录组出快照
- 传出：`CheckItem`，本模块的 frozen dataclass，由包根导出；M4 用它的两个字段构造调度器的 `HeartbeatTrigger`
- 没有新的 Pydantic 模型：快照和检查项只有 M4 的工作流组装和读取，和 `ThesisUpdate`、调度器的 `QueueSnapshot` 一样放在模块里

## Goals / Non-Goals

**Goals:**

- 一个纯函数，输入一份快照，输出按顺序排好的检查项；spec 每张表的每一行都有测试
- 顺延不靠额外的状态：到期没续期的每次都算到期，在排队或运行的不重复开（proposal 第 3 条）
- 性质测试检查：每只股票最多一个检查项、不和在排队或运行的重复、候选评估不超过上限并且按顺序选、输出顺序固定、每个检查项都能构造成 `HeartbeatTrigger`

**Non-Goals:**

按「编程风格」省略的情况：

- 快照不一致（同一只股票出现两次、既是持仓又是候选、在排队或运行的检查项和股票状态对不上）：不检查，调用方保证；不合法的检查项在 M4 构造 `HeartbeatTrigger` 时报错
- 同一个交易日调用两次：M4 按今天只调用一次；重复调用时在排队或运行的不会重开，只有已经跑完的每周复盘会再开一次
- 错过了一周最后一个交易日的收盘检查（进程整晚不在）：这周不补开每周复盘；复查日、催化剂和新候选下一次照常开
- 每周复盘因系统故障结束：不补开
- 已经过期、还没移出的候选：M4 先移出再取快照；没移出的话算临近过期，它的候选评估开始时已经不是候选，不运行
- 在排队或运行的候选评估的股票已经升级或移出：照样占上限，队列下一次决定就让它不再运行
- 池子已满或当天增删次数用完时寻股：照常开，加入由股票池拒绝
- 复查类案件已经得出续期的结论、还在等人工确认（加仓）时催化剂到期：这次不开，案件结束以后的下一次收盘检查开
- 下一个交易日超出日历的覆盖范围：日历报覆盖范围错误，按系统故障处理
- 运行中改了配置：下一次收盘检查按新的配置

## Decisions

### D1 一个纯函数，放在定时心跳模块

```python
def close_check(
    snapshot: CloseSnapshot, calendar: TradingCalendar, config: HeartbeatConfig
) -> tuple[CheckItem, ...]:
    """这次收盘要开的检查项，按开的顺序。today 不是交易日时抛 ValueError。"""
    if not calendar.is_session(snapshot.today):
        raise ValueError(f"{snapshot.today} 不是交易日")
    items = [
        *_reviews(snapshot, snapshot.holdings, _HOLDING_TYPES),
        *_reviews(snapshot, snapshot.watches, _WATCH_TYPES),
        *_evaluations(snapshot, config),
    ]
    if _exploration_due(snapshot, calendar, config):
        items.append(CheckItem(CaseType.EXPLORATION))
    if _weekly_due(snapshot, calendar):
        items.append(CheckItem(CaseType.WEEKLY_RETROSPECTIVE))
    return tuple(items)
```

- 本变更是架构图里"定时心跳"节点的第二部分：心跳读 PM 写下的日期、按时开案件，和「复查日与催化剂」共用模块和配置
- 没有 service 和 ports：不读时钟、不读存储，今天和其他事实都在快照里；M4 每个交易日收盘后调用一次
- 输出直接按开的顺序排好（spec「开的顺序」）：持仓、关注、候选评估、寻股、每周复盘。队列按优先级排，这个顺序只决定同一优先级里谁先启动
- 考虑过：放进调度器，和队列的决定合成一个。没有采用：一个变更只改一个节点；收盘检查要的论点日期、候选的评分和过期时间、上一次寻股，队列都不需要

### D2 快照和检查项

```python
@dataclass(frozen=True, slots=True)
class CheckItem:
    """定时心跳的一个检查项：开出的案件类型；针对单只股票的带股票，寻股和每周复盘不带。

    和调度器的 HeartbeatTrigger 一一对应，M4 用 HeartbeatTrigger(item.case_type, item.symbol)
    入队；不合法的组合在那时报错，这里不重复检查。
    """

    case_type: CaseType
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class ThesisDates:
    """一只持仓或关注股的论点里的复查日和催化剂截止日期。"""

    symbol: str
    review_day: date
    catalyst_deadline: date


@dataclass(frozen=True, slots=True)
class PoolCandidate:
    """股票池里的一只候选。"""

    symbol: str
    score: int | None  # 最新评分；还没有时为 None
    expires_at: datetime  # 过期时间，带时区
    new: bool  # 新候选：加入或代码触发的清仓转为候选以后还没有候选评估得出结论；降为候选的不算


@dataclass(frozen=True, slots=True)
class CloseSnapshot:
    """收盘检查用的一份快照，由调用方在收盘以后从各模块组出。"""

    today: date  # 刚收盘的交易日
    holdings: tuple[ThesisDates, ...]
    watches: tuple[ThesisDates, ...]
    candidates: tuple[PoolCandidate, ...]  # 股票池里的全部候选
    # 最近一次得出结论的寻股，结论时刻归属的交易日；还没有时为 None
    last_exploration_day: date | None
    in_flight: frozenset[CheckItem]  # 定时心跳开出的、在排队或运行的案件
```

- 在排队或运行的心跳案件用同一个 `CheckItem` 表示：排队的取触发的两个字段，运行中的取开始时确定的类型和股票（七种心跳类型只会由心跳触发开出，开始时类型不变）
- 持仓和关注分成两个元组：不用另带股票状态、再检查状态只能是持仓或关注
- 新候选由调用方给出（proposal 第 5 条）：判断要看案件记录，股票池不加字段
- `in_flight` 只用来判断"在不在"和计数，不遍历出输出：frozenset 的遍历顺序随字符串哈希变化，输出的顺序全部来自排序
- 考虑过：直接输出调度器的 `HeartbeatTrigger`。没有采用：模块之间不能互相 import
- 考虑过：把 `HeartbeatTrigger` 移到 contracts，两边共用。没有采用：要改调度器的代码，本变更只改定时心跳一个节点；两边字段相同，M4 一行就能转换。以后别的模块也要用时，再由一个「修改：调度器」移过去

### D3 复查类案件

```python
_REVIEW_TYPES: Final = frozenset(
    {
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.WATCH_REVIEW,
        CaseType.WATCH_CATALYST_REVIEW,
    }
)
# 复查日到期开的、催化剂截止日期到期开的
_HOLDING_TYPES: Final = (CaseType.HOLDING_REVIEW, CaseType.HOLDING_CATALYST_REVIEW)
_WATCH_TYPES: Final = (CaseType.WATCH_REVIEW, CaseType.WATCH_CATALYST_REVIEW)


def _reviews(
    snapshot: CloseSnapshot,
    stocks: tuple[ThesisDates, ...],
    types: tuple[CaseType, CaseType],
) -> list[CheckItem]:
    """到期的复查类案件，按股票代码；每只股票最多一个，催化剂到期时开催化剂复查。"""
    review, catalyst_review = types
    busy = {i.symbol for i in snapshot.in_flight if i.case_type in _REVIEW_TYPES}
    items: list[CheckItem] = []
    for stock in sorted(stocks, key=lambda s: s.symbol):
        if stock.symbol in busy:
            continue
        if stock.catalyst_deadline <= snapshot.today:
            items.append(CheckItem(catalyst_review, stock.symbol))
        elif stock.review_day <= snapshot.today:
            items.append(CheckItem(review, stock.symbol))
    return items
```

- 到期是"不晚于今天"，不是"等于今天"：错过的收盘检查、没有结论的案件，日期没续期就一直到期
- 催化剂先判断：两项都到期时只开催化剂复查（已决定第 1 条）；续期时 review-dates 要求复查日在今天之后至少 3 个交易日，到期的催化剂必须换新的，两项一次处理完
- 四种复查类案件任何一种在排队或运行都挡（第 4 条），不只挡同一种：在跑的案件续期时会处理另一项
- 今天是刚收盘的交易日，不是现在归属的交易日：收盘以后现在归属下一个交易日，用它的话明天到期的复查日今天收盘就开了，提前一个交易日

### D4 顺延不单独记

没有专门的代码：D3 的"不晚于今天"加上"在排队或运行的不开"就是顺延（第 3 条）。spec「没有结论时顺延」的四种情况：

| 案件之后的情况 | 为什么 |
| --- | --- |
| 还在排队或运行 | 在 `in_flight` 里，不再开；降级期间队列不启动，恢复以后按优先级运行 |
| 没有结论就结束 | 论点没变，日期仍然不晚于今天，下一次再开 |
| 续期了 | review-dates 保证新的日期在今天之后 |
| 有结论但没续期，状态没变 | 论点没变，下一次再开（第 12 条） |

- 考虑过：记下降级期间到期的事项，到恢复后的第一个交易日（`recovery_day`）再开。没有采用：要多存一份状态、恢复时清掉；降级期间开出的案件本来就在队列里，恢复以后马上按优先级运行，比恢复后的第一个交易日收盘更早；没有结论的事项日期没变，下一次收盘检查自然再开
- 考虑过：按结束原因判断要不要再开。没有采用：收盘检查只看日期就够了，结束原因不用进快照

### D5 候选评估

```python
def _evaluations(snapshot: CloseSnapshot, config: HeartbeatConfig) -> list[CheckItem]:
    """新候选的候选评估：在排队或运行的也占每天的上限，其余按 spec 的顺序选。"""
    queued = {i.symbol for i in snapshot.in_flight if i.case_type is CaseType.CANDIDATE_EVALUATION}
    slots = max(config.max_daily_evaluations - len(queued), 0)
    waiting = sorted(
        (c for c in snapshot.candidates if c.new and c.symbol not in queued),
        key=_evaluation_order,
    )
    return [CheckItem(CaseType.CANDIDATE_EVALUATION, c.symbol) for c in waiting[:slots]]


def _evaluation_order(candidate: PoolCandidate) -> tuple[bool, int, datetime, str]:
    """没有评分的在最后；评分从高到低；评分相同的先过期的在前；再按股票代码。"""
    score = candidate.score
    return (score is None, -(score or 0), candidate.expires_at, candidate.symbol)
```

- 在排队或运行的候选评估占上限（第 6 条）：预算用完或降级时不会每天再积压 3 个
- `max(..., 0)`：在排队的超过上限（比如调低了配置）时不开，也避免负数切片从末尾取
- 选不上的不记下来：它们仍是新候选，下一次收盘检查重新排
- 考虑过：上限只数这次开的。没有采用（第 6 条）
- 考虑过：按成为候选的先后。没有采用：Proposal 写按最新评分从高到低；评分相同时先过期的先开（第 7 条），快照里已经有过期时间，不用另带成为候选的时刻

### D6 寻股

```python
_STRONG_SCORE: Final = 1  # 好候选：最新评分 ≥ 1，Proposal 的定义


def _exploration_due(
    snapshot: CloseSnapshot, calendar: TradingCalendar, config: HeartbeatConfig
) -> bool:
    """三个条件任一项满足，并且没有寻股在排队或运行、不在冷却期内。"""
    if CheckItem(CaseType.EXPLORATION) in snapshot.in_flight:
        return False
    last = snapshot.last_exploration_day
    if last is not None:
        resume = calendar.add_sessions(last, config.exploration_cooldown_sessions).day
        if snapshot.today < resume:
            return False
    candidates = snapshot.candidates
    viable = sum(1 for c in candidates if c.score is None or c.score >= 0)
    strong = sum(1 for c in candidates if c.score is not None and c.score >= _STRONG_SCORE)
    horizon = calendar.add_sessions(snapshot.today, config.expiring_sessions).close
    expiring = sum(1 for c in candidates if c.expires_at <= horizon)
    return (
        viable < config.min_viable_candidates
        or strong < config.min_strong_candidates
        or expiring * 2 > len(candidates)
    )
```

- 有效候选是评分为空或不为负的（第 9 条）：负分表示 PM 看淡；还没有评分的（手动添加）算有效
- "评分 ≥ 1"和"超过一半"是 Proposal 的字面意思，不做成配置；"设定数量""下限""冷却期"和"临近"的期限是阈值，做成配置
- "超过一半"用 `expiring * 2 > len(candidates)`，不用除法；没有候选时是 0 > 0，不满足，由有效候选不足（0 只）开寻股
- 临近过期和候选的过期时间比：过期时间就是某个交易日的收盘时刻（stock-pool），和今天之后第 3 个交易日的收盘时刻比，含当天
- 冷却期从上一次得出结论的寻股那一天算（第 10 条）：那一天可以晚于今天（今天收盘后得出的结论），这时 `resume` 也晚于今天，在冷却期内，不需要特判
- 考虑过：冷却期从开寻股的那天算。没有采用（第 10 条）：在队列里等了几天的寻股刚跑完就能再开；系统故障的寻股也会挡住下一次
- 考虑过：输出这次为什么寻股。没有采用：现在没有读者，M5 写寻股的上下文时再加

### D7 每周复盘

```python
def _weekly_due(snapshot: CloseSnapshot, calendar: TradingCalendar) -> bool:
    """今天是这周最后一个交易日：下一个交易日在下一周（ISO 周，周一到周日）。"""
    if CheckItem(CaseType.WEEKLY_RETROSPECTIVE) in snapshot.in_flight:
        return False
    following = calendar.next_session(snapshot.today).day
    return following.isocalendar()[:2] != snapshot.today.isocalendar()[:2]
```

- 比较 ISO 的年和周：跨年也对，比如 2026-12-31（2026 年第 53 周）和 2027-01-04（2027 年第 1 周）
- 不做成配置（第 11 条）
- 考虑过：在 shared 的交易日历里加"这周最后一个交易日"。没有采用：只有这里用，两行就能算

### D8 配置

`HeartbeatConfig` 增加五项，放在复查日与催化剂的配置项后面：

```python
# 在排队或运行的候选评估加上这次开的，不超过这么多个
max_daily_evaluations: int = Field(default=3, ge=1)
# 有效候选（最新评分为空或不低于 0）少于这么多只时寻股
min_viable_candidates: int = Field(default=5, ge=1)
# 最新评分 ≥ 1 的候选少于这么多只时寻股
min_strong_candidates: int = Field(default=2, ge=1)
# 过期时间不晚于今天之后第这么多个交易日收盘时刻的候选算临近过期
expiring_sessions: int = Field(default=3, ge=1)
# 今天早于上一次寻股的那一天之后第这么多个交易日时不寻股
exploration_cooldown_sessions: int = Field(default=3, ge=1)
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 每天的候选评估上限 | 3 个 | 寻股一次最多加入 10 只，两次寻股的收盘检查至少相隔 4 个交易日，平均每天不到 3 只，积压不会变多；候选 10 个交易日后过期，过期前能评估 30 只，等于池子容量；和默认并发上限 3 相同，一天的候选评估能一起跑（第 6 条） |
| 有效候选的下限 | 5 只 | 关注名额 8 只，候选少于 5 只时补充关注的来源不够；寻股一次加入几只就能回到下限以上，不会冷却期一过就又触发（第 9 条） |
| 好候选的下限 | 2 只 | 只剩 1 只好候选时升级全靠它（第 9 条） |
| 临近过期的期限 | 3 个交易日 | 和冷却期相同：这些候选在下一次能寻股以前就会被移出（第 9 条） |
| 寻股的冷却期 | 3 个交易日 | Proposal 的例子（第 10 条） |

- 都只要求至少 1：0 的话上限、下限和期限都没有意义；配置项之间没有必须满足的关系，不加校验
- 不把好候选的评分界线做成配置：见 D6

### D9 M4 的接法（给 M4 的对照）

本变更不调用其他模块，下表是 M4 工作流的接法：

| 什么时候 | 做什么 |
| --- | --- |
| 每个交易日收盘以后，一次 | 先移出到期的候选（股票池的 `expire_candidates`）；组快照、调用 `close_check`；按输出的顺序把每个检查项构造成 `HeartbeatTrigger(item.case_type, item.symbol)` 入队，触发时刻都是这次的时刻；最后取得队列的决定。按今天去重，同一个交易日只做一次 |
| 今天 | `calendar.last_session_before(clock.now()).day` |
| 持仓、关注 | 投资论点、股票池里关注论点的 `review_day` 和 `catalyst.deadline` |
| 候选 | 股票池里状态是候选的记录：`score`、`expires_at`；新候选从案件记录判断：成为候选的时刻以后，这只股票还没有得出结论的候选评估案件 |
| 上一次寻股的那一天 | 案件记录里最近一个得出结论的寻股：`calendar.session_closing_after(结论时刻).day` |
| 在排队或运行的心跳案件 | 排队的案件里触发是 `HeartbeatTrigger` 的，取它的两个字段；运行中的案件类型是七种心跳类型之一的，取 `case_type` 和 `symbol` |

- 降级和预算不进快照：收盘检查照常开，队列决定什么时候启动
- 收盘对账（M2）和收盘检查的先后不影响结果：对账不改论点和股票池

### D10 包根导出和纯区域

包根增加导出 `CheckItem`、`CloseSnapshot`、`PoolCandidate`、`ThesisDates`、`close_check`；包根和 `domain/__init__.py` 的 docstring 加上收盘检查。没有 service 和 ports。

- `domain/close_check.py` 只用 `dataclasses`、`datetime`、`typing`，以及 contracts 的 `case`、shared 的 `trading_calendar` 和本模块的 config；`config.py` 不加 import。都在白名单里，不改 `tests/architecture/rules.py`
- 不用改 import-linter 的配置：模块里没有新的层

### D11 测试

```
tests/modules/heartbeat/builders.py                            增加 close-check spec 的基准数据
tests/modules/heartbeat/test_config.py                         增加新配置项的场景
tests/modules/heartbeat/domain/test_close_check.py             spec 其余场景
tests/modules/heartbeat/domain/test_close_check_properties.py  性质测试
```

- builders：基准快照和"复查日改为""截止日期改为""评分改为""过期时间改为""是新候选"等辅助函数，用 `dataclasses.replace`；交易日历用 `tests/fakes/calendar.py` 的样例日历（覆盖到 2027-01-29，场景最晚用到 2027-01-04）
- 性质测试（Hypothesis）：从样例日历里随机今天（2026-09-15 到 2027-01-15），随机 0 到 5 只持仓和关注、0 到 8 只候选（股票代码互不相同），日期在今天前后 10 个交易日以内，评分为空或 -2 到 2，过期时间是今天之前 2 个到之后 12 个交易日的收盘时刻；随机在排队或运行的检查项（只用这些股票）、上一次寻股的那一天和配置。检查：
  - 每只股票最多一个检查项，寻股和每周复盘各最多一个
  - 没有检查项在 `in_flight` 里；有复查类案件在排队或运行的股票没有复查类检查项
  - 没有被挡的持仓和关注：有日期不晚于今天就有检查项，催化剂截止日期不晚于今天时是催化剂复查
  - 候选评估的个数是"上限减在排队或运行的个数（不小于 0）"和"不在排队或运行的新候选个数"中小的那个，并且是这些新候选按 spec 顺序排在最前的几只
  - 输出按持仓、关注、候选评估、寻股、每周复盘分组，持仓和关注里按股票代码
  - 打乱快照里各元组的顺序，输出不变
  - 每个检查项都能构造成调度器的 `HeartbeatTrigger`（测试不受模块之间不能 import 的限制）
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [不续期的结论没有执行成时每天都开复查类案件] → PM 在上下文里看得到上次的结果和拒绝原因，续期就不再到期；没有续期的论点本来就不该一直不复查
- [降级好几天，恢复以后积压的复查类案件一起跑] → 每只股票最多一个；队列按优先级和并发上限启动，持仓在前
- [新候选积压到过期] → 在排队的占上限，积压不变多；先过期的先开；过期前能评估 30 只
- [寻股的条件和默认值是估计的] → 都可配置；M6 复盘时按实际触发的频率调
- [候选股事件得出结论以后还会开候选评估] → 多一个候选评估，按候选评估的问题再看一次；只有候选评估的结论算（第 5 条）
- [同一个交易日调用两次时每周复盘可能重开] → M4 按今天去重

## Migration Plan

只新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
