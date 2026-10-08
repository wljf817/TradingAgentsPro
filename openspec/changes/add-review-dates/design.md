# Design

## Context

现状（M2 进行中，M3 完成了股票池、案件类型与结论、案件队列、案件阶段）：

- 论点的格式在 `contracts/thesis.py`：`WatchThesis` 和 `InvestmentThesis` 都有 `review_day`（`date`，必填）和 `catalyst`（`description`、`deadline`、`reference: CatalystReference | None`，引用的 `kind` 是日程项或事件）。都是 frozen 的 Pydantic 模型，字段全部相同时 `==` 为真
- 结论在 `contracts/conclusion.py`，每份结论最多附一份论点：持仓的结论的 `thesis`（论点更新）或 `watch_thesis`（清仓转为关注），关注的结论的 `thesis`（开仓方案）或 `watch_thesis`（论点更新），候选的结论的 `watch_thesis`（升级）
- 调度器的 `conclusion_rejection` 按案件类型检查结论，拒绝作为返回值；第 3 项用"复查类案件，并且决定是持有、加仓、减仓或继续关注"判断续期。案件状态的 `reject_conclusion` / `accept_conclusion` 只接收校验的结果
- 交易日历有 `is_session`、`add_sessions`（之后第 n 个交易日，不含当天）和 `session_closing_after`（时刻归属的交易日）；超出覆盖范围的日期报 `CalendarRangeError`。生产环境的日历覆盖到启动后两年
- 股票池记录里的"上次复查时间"是 PM 最近一次给出任何结论的时刻，不是本变更的"上次复查的那一天"（只在新设和续期时变）
- M3 只写纯逻辑和测试：不接外部系统，不建表；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/review-dates/spec.md。

### 模块边界

```
contracts（不改）
  thesis.py   WatchThesis、InvestmentThesis、Catalyst、CatalystReference、ReferenceKind
              <- 本模块的输入：M4 从 PM 的结论和存着的当前论点里取出

shared（不改）
  trading_calendar.py  TradingCalendar：is_session、add_sessions

modules/heartbeat（新增）
  domain/dates.py  ThesisUpdate、ThesisDatesRejection、thesis_dates_rejection、
                   last_review_day_after
  config.py        HeartbeatConfig
  __init__.py      导出上面的名字
```

本次只新增定时心跳一个模块，不改其他模块。跨模块传递的 Pydantic 模型：

- 传入：`WatchThesis`、`InvestmentThesis`（新写的论点和原来的论点）；其余输入是 `date`、`Decimal`、`bool` 和交易日历
- 传出：`ThesisDatesRejection`（StrEnum）和 `date`
- `ThesisUpdate` 是本模块的 frozen dataclass，由包根导出。拒绝原因和调度器的 `ConclusionRejection` 一样放在模块里：只有 M4 的工作流读它，退回 PM 时作为原因

## Goals / Non-Goals

**Goals:**

- 一个纯函数给出第一项不通过的原因，一个纯函数给出之后的上次复查的那一天；spec 每张表的每一行都有测试
- 性质测试检查已决定的第 1 条：不论怎样调整，通过检查的论点，复查日都不晚于上次复查那天之后第 20 个交易日

**Non-Goals:**

按「编程风格」省略的情况：

- 买单迟迟不成交：复查日和上次复查的那一天都从给出结论那天算，成交晚了，第一次复查就早几天
- 运行中改了配置，原来的复查日超出新的上限：原样保留的不检查，提前时不查上限
- 日程项改期：原样保留的催化剂不再检查，下一次改催化剂时按新日期检查
- 引用的日程项在日历覆盖范围（启动后两年）之外：日历报覆盖范围错误，按系统故障处理
- 关联的事件不是本案件的事件、事件 ID 不存在：不检查
- 有固定日期的催化剂没有引用日程项：发现不了，靠 PM 的提示和复盘
- 描述只改几个字就算新的催化剂：不判断文字的内容
- 引用从日程项换成事件、从事件换成不引用：按改了的催化剂检查，不另设规则
- 浮动盈亏传给了关注论点、日程项的日期传给了不引用日程项的催化剂：忽略
- 同一只股票同时有两份论点更新：同一只股票同一时间只有一个运行中的案件（case-queue），调用方串行

## Decisions

### D1 新模块 heartbeat，只有 domain 和 config

- 本变更是架构图里"定时心跳"节点的第一部分：心跳读取 PM 写下的日期，下一个变更「收盘检查」按这些日期判断到期，两者共用配置，所以放在同一个模块
- 没有 service 和 ports：不读时钟、不读存储，"今天"作为参数传入
- 考虑过：放进调度器的 `conclusion_rejection`。没有采用：一个变更只改一个节点；这项检查要原来的论点、上次复查的那一天、浮动盈亏和日程项的日期，结论的校验都没有
- 考虑过：在 contracts 里加拒绝原因。没有采用：和 `ConclusionRejection` 一样只有 M4 读

### D2 输入

```python
type Thesis = WatchThesis | InvestmentThesis


@dataclass(frozen=True, slots=True)
class ThesisUpdate:
    """PM 新写的一份论点，以及检查它的日期要用到的事实。"""

    thesis: Thesis
    # 原来的论点和上次复查的那一天；新设（买入、升级、清仓转为关注）时都是 None
    previous: Thesis | None = None
    last_review_day: date | None = None
    renewal: bool = False  # 复查类案件里的续期
    # 持仓的浮动盈亏比例 = (最新价 - 均价) / 均价；只有投资论点的平时调整用到，None 是查不到
    pnl_ratio: Decimal | None = None
    # 新论点的催化剂引用日程项时，这个日程项的日期；None 是查不到
    schedule_day: date | None = None

    def __post_init__(self) -> None:
        if (self.previous is None) != (self.last_review_day is None):
            raise ValueError("原来的论点和上次复查的那一天必须同时传入")
        if self.previous is None:
            if self.renewal:
                raise ValueError("续期一定有原来的论点")
        elif type(self.previous) is not type(self.thesis):
            raise ValueError("原来的论点和新论点不是同一种论点")
```

- 情形由两项推出：没有原来的论点就是新设；有原来的论点时，`renewal` 分开续期和平时调整。不另设情形的枚举，调用方也就不会传出矛盾的组合
- 今天由调用方用 `session_closing_after(now).day` 算好传入，`thesis_dates_rejection` 检查它是交易日
- 浮动盈亏比例由调用方从账户数据算好：本模块不碰账户数据。`None` 在投资论点的平时调整里表示查不到，按亏损处理（spec「亏损时不能推后复查日」）
- 日程项的日期只有一个：一份论点只有一个催化剂
- 考虑过：一个函数带八九个参数。没有采用：一份论点的事实放在一起，测试的基准数据也好写

### D3 复查日

```python
class ThesisDatesRejection(StrEnum):
    """论点里的日期不通过的原因，顺序见 review-dates spec「检查的顺序」。"""

    REVIEW_TOO_SOON = "review_too_soon"  # 复查日太近
    POSTPONED_IN_LOSS = "postponed_in_loss"  # 亏损时不能推后复查日
    REVIEW_TOO_LATE = "review_too_late"  # 复查日太远
    REVIEW_NOT_SESSION = "review_not_session"  # 复查日不是交易日
    STALE_CATALYST = "stale_catalyst"  # 没有写新的催化剂
    DEADLINE_TOO_SOON = "deadline_too_soon"  # 截止日期太近
    UNKNOWN_SCHEDULE = "unknown_schedule"  # 引用的日程项不存在
    OFF_SCHEDULE = "off_schedule"  # 截止日期和日程项不符
    DEADLINE_TOO_LATE = "deadline_too_late"  # 截止日期太远
    DEADLINE_NOT_SESSION = "deadline_not_session"  # 截止日期不是交易日


def thesis_dates_rejection(
    update: ThesisUpdate, today: date, calendar: TradingCalendar, config: HeartbeatConfig
) -> ThesisDatesRejection | None:
    """按 spec「检查的顺序」给出第一项不通过的原因，全部通过时为 None。

    today 是现在归属的交易日，不是交易日时抛 ValueError。拒绝是 PM 提交的正常结果，作为返回值。
    """
    if not calendar.is_session(today):
        raise ValueError(f"{today} 不是交易日")
    rejection = _review_day_rejection(update, today, calendar, config)
    if rejection is not None:
        return rejection
    return _catalyst_rejection(update, today, calendar, config)


def _review_day_rejection(
    update: ThesisUpdate, today: date, calendar: TradingCalendar, config: HeartbeatConfig
) -> ThesisDatesRejection | None:
    day = update.thesis.review_day
    previous = update.previous
    if previous is None or update.renewal:
        # 新设和续期：今天之后第 min 到第 max 个交易日
        if day < calendar.add_sessions(today, config.min_review_sessions).day:
            return ThesisDatesRejection.REVIEW_TOO_SOON
        # 亏损的持仓续期：不晚于今天之后第 default 个交易日。新设还没有持仓
        if (
            previous is not None
            and _in_loss(update, config)
            and day > calendar.add_sessions(today, config.default_review_sessions).day
        ):
            return ThesisDatesRejection.POSTPONED_IN_LOSS
        if day > calendar.add_sessions(today, config.max_review_sessions).day:
            return ThesisDatesRejection.REVIEW_TOO_LATE
    elif day == previous.review_day:
        return None  # 平时调整里原样保留，不检查
    else:
        # 平时调整：最早调到下一个交易日；推后的不晚于上次复查那天之后第 max 个交易日
        if day <= today:
            return ThesisDatesRejection.REVIEW_TOO_SOON
        if day > previous.review_day:
            if _in_loss(update, config):
                return ThesisDatesRejection.POSTPONED_IN_LOSS
            assert update.last_review_day is not None  # 和 previous 同时传入
            limit = calendar.add_sessions(update.last_review_day, config.max_review_sessions)
            if day > limit.day:
                return ThesisDatesRejection.REVIEW_TOO_LATE
    if not calendar.is_session(day):
        return ThesisDatesRejection.REVIEW_NOT_SESSION
    return None


def _in_loss(update: ThesisUpdate, config: HeartbeatConfig) -> bool:
    """持仓亏损超过阈值；查不到浮动盈亏时按亏损处理。关注论点没有持仓。"""
    if isinstance(update.thesis, WatchThesis):
        return False
    return update.pnl_ratio is None or update.pnl_ratio < -config.max_postpone_loss
```

- 先比较范围、再查交易日：PM 写的日期可能远超日历的覆盖范围，比较范围只用到从今天和上次复查那天推出的日期；查交易日时日期已经在范围内，不会报覆盖范围错误
- 续期和新设走同一条路，不要求复查日和原来的不同（proposal「需要人决定」第 13 条）；亏损的持仓续期时复查日不晚于默认间隔（第 9 条），新设还没有持仓，不看亏损
- 平时调整只有推后才查上限和亏损：提前的日期早于原来的，原来的已经在上限以内
- 推后的上限从上次复查那天算（已决定第 1 条），所以平时调整怎样改都不会让复查日晚于上次复查那天之后第 20 个交易日，D9 的性质测试检查这一点
- 浮动盈亏用严格的"小于"：等于 −0.05 时可以推后，和 Proposal 的"超过一定幅度"一致

### D4 催化剂

```python
def _catalyst_rejection(
    update: ThesisUpdate, today: date, calendar: TradingCalendar, config: HeartbeatConfig
) -> ThesisDatesRejection | None:
    catalyst = update.thesis.catalyst
    if update.previous is not None:
        old = update.previous.catalyst
        kept = catalyst == old or _links_event(catalyst, old)
        expired = old.deadline <= today
        if kept and not (expired and update.renewal):
            return None  # 原样保留或只关联事件，不检查截止日期
        if expired and not _is_new(catalyst, old):
            return ThesisDatesRejection.STALE_CATALYST
    return _deadline_rejection(catalyst, update.schedule_day, today, calendar, config)


def _links_event(new: Catalyst, old: Catalyst) -> bool:
    """只关联事件：描述和截止日期不变，原来没有引用，现在引用一个事件。"""
    return (
        new.description == old.description
        and new.deadline == old.deadline
        and old.reference is None
        and new.reference is not None
        and new.reference.kind is ReferenceKind.EVENT
    )


def _is_new(new: Catalyst, old: Catalyst) -> bool:
    """新的催化剂：截止日期变了，描述或引用也至少变了一项。"""
    return new.deadline != old.deadline and (
        new.description != old.description or new.reference != old.reference
    )


def _deadline_rejection(
    catalyst: Catalyst,
    schedule_day: date | None,
    today: date,
    calendar: TradingCalendar,
    config: HeartbeatConfig,
) -> ThesisDatesRejection | None:
    deadline = catalyst.deadline
    if deadline <= today:
        return ThesisDatesRejection.DEADLINE_TOO_SOON
    reference = catalyst.reference
    if reference is not None and reference.kind is ReferenceKind.SCHEDULE:
        if schedule_day is None:
            return ThesisDatesRejection.UNKNOWN_SCHEDULE
        first = calendar.add_sessions(schedule_day, 1).day
        last = calendar.add_sessions(schedule_day, config.max_schedule_lag_sessions).day
        if not first <= deadline <= last:
            return ThesisDatesRejection.OFF_SCHEDULE
    elif deadline > calendar.add_sessions(today, config.max_deadline_sessions).day:
        return ThesisDatesRejection.DEADLINE_TOO_LATE
    if not calendar.is_session(deadline):
        return ThesisDatesRejection.DEADLINE_NOT_SESSION
    return None
```

- 一张表的四格（续期或平时调整 × 到期或没到期）合成两个判断：保留（原样或只关联事件）在续期遇到到期时不算数；到期以后改了的必须是新的。续期遇到到期时，保留的催化剂截止日期没变，一定不是新的，所以给出"没有写新的催化剂"
- "新的催化剂"要求截止日期也变（proposal「需要人决定」第 10 条）：到期的截止日期不晚于今天，新的截止日期反正要在今天之后，这一条只是让"只关联事件"和"只改描述"不算新的
- 引用日程项的截止日期是日程项之后第 1 到第 2 个交易日之间的日期，再查是不是交易日：第 1 和第 2 个交易日之间隔着周末时，周末给出"不是交易日"
- 不引用日程项的才查 60 个交易日的上限（第 7 条）；引用日程项的截止日期跟着日程项走
- 比较催化剂用 Pydantic 模型的 `==`：字段全部相同才相等，引用的种类和 ID 都在里面

### D5 上次复查的那一天

```python
def last_review_day_after(update: ThesisUpdate, today: date) -> date:
    """论点通过检查、生效以后的上次复查的那一天：新设和续期是今天，平时调整不变。"""
    if update.last_review_day is None or update.renewal:
        return today
    return update.last_review_day
```

- 调用方只在论点通过检查以后调用；不通过时论点不生效，存着的上次复查的那一天不变
- 和检查分成两个函数：检查不通过时没有"之后"可言，合在一起的话返回值要么是联合类型，要么带一个只在通过时有意义的字段
- 买入那一天是给出开仓方案结论的那一天（第 14 条）：检查发生在结论通过的时候，用的就是那时的今天

### D6 配置

```python
class HeartbeatConfig(BaseModel):
    """定时心跳的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 新设和续期的复查日在今天之后第 min 到第 max 个交易日之间；
    # 平时调整推后的复查日不晚于上次复查那天之后第 max 个交易日
    min_review_sessions: int = Field(default=3, ge=1)
    max_review_sessions: int = Field(default=20, ge=1)
    # 亏损的持仓续期时复查日不晚于今天之后第这么多个交易日；也写进 PM 的提示，作为一般情况下的复查间隔
    default_review_sessions: int = Field(default=5, ge=1)
    # 持仓的浮动盈亏比例低于它的相反数时，平时调整不能推后复查日，续期按默认间隔复查
    max_postpone_loss: Decimal = Field(default=Decimal("0.05"), gt=0, lt=1)
    # 引用日程项的催化剂，截止日期在日程项之后第 1 到第这么多个交易日
    max_schedule_lag_sessions: int = Field(default=2, ge=1)
    # 不引用日程项的催化剂，截止日期不晚于今天之后第这么多个交易日
    max_deadline_sessions: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def _check_review_range(self) -> Self:
        if self.min_review_sessions > self.default_review_sessions:
            raise ValueError("复查日的默认间隔不能小于最短间隔")
        if self.default_review_sessions > self.max_review_sessions:
            raise ValueError("复查日的默认间隔不能大于最长间隔")
        return self
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 复查日最短间隔 | 3 个交易日 | Proposal 规定 |
| 复查日最长间隔 | 20 个交易日 | Proposal 规定；推后的上限也用它（已决定第 1 条） |
| 复查日默认间隔 | 5 个交易日 | Proposal 规定；复查日保持必填，这一项用于亏损的持仓续期（第 9 条），也由 M5 写进 PM 的提示（第 3 条） |
| 不能推后的亏损幅度 | 0.05 | 默认紧急阈值 8%、基准止损距离 10%，跌 5% 约走到止损的一半（第 8 条） |
| 日程项后的截止期限 | 2 个交易日 | Proposal 的"事件后一两天"，按交易日算（第 6 条） |
| 催化剂截止日期最长间隔 | 60 个交易日 | 和跟进触发时间的上限相同，约一个财报季（第 7 条） |

- 三个复查间隔是同一个范围，最短 ≤ 默认 ≤ 最长要校验：最短大于最长时任何复查日都通不过，每个复查案件都会重交到结论无效
- 下一个变更「收盘检查」的阈值（候选评估上限、寻股触发等）加在同一个类里

### D7 M4 的接法（给 M4 的对照）

本变更不调用其他模块，下表是 M4 工作流的接法：

| 什么时候 | 做什么 |
| --- | --- |
| PM 提交结论 | 先 `conclusion_rejection`；通过以后，结论附了论点就组 `ThesisUpdate`，调用 `thesis_dates_rejection`。任何一个不通过：`reject_conclusion(config)`，带着原因退回 PM；都通过：`accept_conclusion(action)` |
| 取出要检查的论点 | 持仓的结论：`thesis`（论点更新）或 `watch_thesis`（清仓转为关注，新设）；关注的结论：`thesis`（开仓方案，新设）或 `watch_thesis`（论点更新）；候选的结论：`watch_thesis`（升级，新设） |
| 组 `ThesisUpdate` | 论点更新带这只股票现在的论点和它的上次复查的那一天；`renewal` 用 `conclusion_rejection` 第 3 项的同一个条件（复查类案件，并且决定是持有、加仓、减仓或继续关注）；投资论点的论点更新带账户数据算出的浮动盈亏比例；催化剂引用日程项时按 ID 查日期 |
| 论点生效 | 存论点和 `last_review_day_after(update, today)`：开仓方案的投资论点在买入成交时生效；升级和关注论点更新时，经股票池新加的"更新关注论点"操作存进股票池（M4 的「修改：股票池」）；清仓转为关注的关注论点在清仓完成时生效 |

- 今天在 PM 提交结论时算一次：`calendar.session_closing_after(clock.now()).day`
- 退回 PM 时给出拒绝原因，以及 M5 上下文里的今天、上次复查的那一天和推后的上限，PM 照着改一次就能通过

### D8 包根导出和纯区域

包根导出 `HeartbeatConfig`、`ThesisDatesRejection`、`ThesisUpdate`、`last_review_day_after`、`thesis_dates_rejection`。没有 service 和 ports。

- `domain/dates.py` 只用 `dataclasses`、`datetime`、`decimal`、`enum`，以及 contracts 的 `thesis`、shared 的 `trading_calendar` 和本模块的 config；`config.py` 只用 `decimal`、`typing` 和 pydantic。都在白名单里，不改 `tests/architecture/rules.py`
- 新模块不用改 import-linter 的配置：`module-layers` 契约按 `tradingagentspro.modules.*` 匹配，`acyclic` 契约按包根检查

### D9 测试

```
tests/modules/heartbeat/builders.py                      spec 的基准数据
tests/modules/heartbeat/test_config.py                   spec「复查日与催化剂的配置」的前两个场景
tests/modules/heartbeat/domain/test_dates.py             spec 其余场景
tests/modules/heartbeat/domain/test_dates_properties.py  性质测试
```

- builders 用 `tests/fakes/calendar.py` 的样例日历，基准论点从 stock-pool 和 case-types 的基准数据复制；"复查日改为""截止日期改为"用 `model_copy(update=...)` 写成辅助函数
- 没有写明论点种类的场景，用 pytest 参数化对关注论点和投资论点各跑一遍
- 性质测试（Hypothesis）：从样例日历里随机一个今天，新设一份论点；之后随机一串步骤，每步把今天往后推 0 到 25 个交易日，随机取续期或平时调整、复查日（今天前后 −5 到 +30 个交易日，或者附近的周末）、催化剂（原样保留、只关联事件、只改截止日期、换成新的、引用日程项）和浮动盈亏比例；只有通过的论点才生效，并按 `last_review_day_after` 更新上次复查的那一天。每步以后检查：
  - 当前论点的复查日不晚于上次复查那天之后第 20 个交易日（已决定第 1 条）
  - 通过检查、并且改了的复查日和截止日期都是交易日，都在今天之后
  - 投资论点的平时调整在亏损时通过的，复查日不晚于原来的
  - 投资论点的续期在亏损时通过的，复查日不晚于今天之后第 5 个交易日（默认间隔）
  - 原来的催化剂已经到期时通过的续期，新的催化剂截止日期变了，描述或引用也变了
- 用 `event` 确认样例里出现过十种拒绝原因和三种情形
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [PM 写到周末或者超出范围被退回，多用一次重交] → M5 的上下文给出今天、上次复查的那一天和推后的上限；重交最多 2 次，用完按没有结论处理，到期事项由收盘检查顺延
- [平时调整里没到期的催化剂可以一直往后改] → 每次都不晚于今天之后 60 个交易日；复查日照样至少每 20 个交易日强制一次复查
- [只比较文字，描述改几个字就算新的催化剂] → 复盘时 Reviewer 看得到前后两份论点；代码不判断内容
- [亏损的持仓续期按默认间隔复查，可能连着几次复查都在亏损区间] → 每次只是多一个复查案件；复查时 PM 也可以减仓或清仓
- [查不到浮动盈亏按亏损处理，行情中断时不能推后复查日] → 提前和原样保留不受影响；行情中断时系统本来就在降级
- [加仓不重新起算，加仓以后很快又到复查日] → 复查日到期时续期，就从那天重新起算

## Migration Plan

只新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
