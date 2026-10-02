# Design

## Context

现状：

- `shared` 只有包说明，没有代码
- Ruff 禁止 `time` 模块和读取当前时间的方法；`per-file-ignores` 只为 `tests/conftest.py` 放行
- 纯区域（`shared`、`contracts`、各模块的 `domain` 和 `config`）的 import 白名单里没有第三方包，也没有 `zoneinfo`
- 测试进程固定在 UTC+14 运行

动机见 proposal.md 的 Why。

**模块边界**：本次不涉及业务模块，也不新增 Pydantic 模型。新类型都在 `shared` 里，用 frozen dataclass 写；适配器在 `integrations` 里。以后 contracts 需要交易日时，用 `date` 或 `datetime` 字段即可。

以下内容已在临时目录验证（2026-10-02，Python 3.13）：

- **exchange_calendars 4.13.2**（2026-03 发布，要求 Python 3.10 及以上）
  - 依赖 pandas（装上的是 3.0.6）、numpy、tzdata 等，没有 `py.typed`
  - 构造 XNYS 2020–2028 的日历约 0.06 秒，共 2261 个交易日
  - `schedule` 的 open、close 列是 UTC 时间；`to_pydatetime()` 得到的时区对象是 `ZoneInfo('UTC')`，不是 `datetime.UTC`
  - 2025–2027 年的休市日、补休、半日市和夏令时切换，与 NYSE 官网公告一致；2025-01-09 的临时休市已收录
  - 在 UTC 和 UTC+14 两个进程时区下，输出完全相同
  - import 时会用 `pd.Timestamp.now()`（本机时间）算出默认的起止日期，但只在调用方不传起止日期时使用
- **mypy strict**：不加设置时报 `import-untyped`；为这个模块设 `follow_untyped_imports = true` 后通过
- **Python 的比较**：用 `bisect` 在 `date` 列表里查 `datetime`、在带时区的时间列表里查不带时区的时间或 `date`，都会直接报 `TypeError`
- **moomoo `request_trading_days`**：只能查到当年 12 月 31 日，不区分半日市，每 30 秒最多调用 30 次

## Goals / Non-Goals

**Goals:**

- 交易日和交易时段的语义由本项目的纯代码定义和测试，包括边界怎么算、含不含当天。日历库只回答"哪天开市、几点开盘、几点收盘"
- 各模块的单元测试使用 FakeClock 和样例日历，不依赖日历库，也不读系统时间
- 计算结果不受进程时区和加载时刻影响

**Non-Goals:**

- 不做多交易所支持。纯计算的日历不绑定交易所，但本次只加载 XNYS
- 不把交易日数据存进数据库。每次启动时从日历库加载
- 不在日历里规定"某个时刻属于哪个交易日"。不同的规则需要不同的答案，由各自的变更决定，见 D4
- 按「简单优先」省略的情况：
  - 日历库给出的数据有误（乱序、开盘晚于收盘等）：不校验
  - 日历库还没收录的临时休市：不手工补充，见 Risks
  - 传错类型的参数：不写检查，比较时自然报 `TypeError`
  - n 小于 1、计数的起点晚于终点、加载范围不合理：不写检查

## Decisions

### D1 时钟：shared 里只放协议，只提供 `now()`

```
shared/clock.py              Clock（Protocol）：now() -> datetime，带时区的 UTC 时间
integrations/system_clock.py SystemClock：datetime.now(UTC)。全项目只有这个文件可以读系统时间
tests/fakes/clock.py         FakeClock：从给定时间开始，可以 set 和 advance
```

- service 和以后的 workflow 接收注入的 Clock；domain 不接收 Clock，"现在"作为参数传入。这条约定 M0 已写进 `config.yaml`
- FakeClock 只保存一个时间，`set` 直接替换，`advance` 加上一个 `timedelta`，不做额外检查。测试传入的一律是 UTC 时间
- Clock 不保证读数单调递增。需要判断先后顺序的规则，用保存下来的截止时刻和当前时间比较
- 考虑过：Clock 同时提供等待（sleep）
  - 没有采用：M1 不需要等待。M2 接入 DBOS 后，工作流里的等待要用 DBOS 的持久化休眠，现在设计等待接口只能靠猜
- 考虑过：SystemClock 放在 bootstrap
  - 没有采用：bootstrap 只负责装配；读取系统时间属于接入外部来源，放在 integrations 才能单独测试。M6 的回放时钟也会放在 integrations

### D2 交易日历是 shared 里的纯计算对象，日历库只负责产出数据

```
integrations/nyse_calendar.py              shared/trading_calendar.py（纯区域）
  用 exchange_calendars 读取 XNYS              TradingSession(day, open, close)
  起止日期由调用方给出        --- 交易时段 -->    TradingCalendar(sessions)
  换算成 datetime.UTC                           用 bisect 查找，不做时区换算

domain 和 service 直接使用 TradingCalendar；单元测试用 tests/fakes 的样例日历
```

理由：

- 纯区域不能 import 第三方包，也不能用 `zoneinfo`，所以日历库只能放在纯区域之外
- 交易时段的开盘和收盘已经是 UTC 时间，纯代码只需要比较先后，用不到时区规则
- 所有查询语义都由本项目定义，写在 spec 里，用 Hypothesis 测试
- domain 的单元测试和正式运行用的是同一个类，只是数据不同，不存在替身和真实实现不一致的问题

纯区域的白名单不需要扩充：用到的 `datetime`、`dataclasses`、`bisect`、`typing`、`collections.abc` 都已经在白名单里。

- 考虑过：shared 里只定义协议，由 exchange_calendars 的适配器实现全部查询
  - 没有采用：查询语义会由日历库决定（比如它按分钟查询时的 `side` 约定和方向参数）；domain 的测试要么用替身（可能和真实行为不一致），要么加载 pandas
- 考虑过：在纯代码里手写节假日规则
  - 没有采用：`config.yaml` 规定交易日用交易所日历库；复活节和补休规则维护成本高

### D3 类型

- `TradingSession` 是 `frozen=True, slots=True` 的 dataclass，字段是 `day: date`、`open: datetime`、`close: datetime`
  - 类名不叫 `Session`，以免和以后数据库的 Session 混淆
  - 字段名用 `day`，以免和 `date` 类型重名
- `TradingCalendar(sessions: Iterable[TradingSession])` 把输入存成元组，不做校验。数据来自日历库或测试里的样例，按交易日排好序、时间是 UTC，这是调用方的约定
- 不写类型检查：传错类型时（日期参数传了 `datetime`，时刻参数不带时区或传了 `date`），`bisect` 比较时会直接报 `TypeError`
- 超出覆盖范围报 `CalendarRangeError`，它是 `LookupError` 的子类。单独定义一个类，调用方可以只捕获它

### D4 查询接口和语义

| 方法 | 返回 | 语义 |
| --- | --- | --- |
| `is_session(day)` | `bool` | day 是不是交易日 |
| `session(day)` | `TradingSession \| None` | day 的交易时段；不是交易日时为 None |
| `next_session(day)` | `TradingSession` | day 之后的第一个交易日，不含 day |
| `previous_session(day)` | `TradingSession` | day 之前的最后一个交易日，不含 day |
| `add_sessions(day, n)` | `TradingSession` | day 之后第 n 个交易日，n ≥ 1；`add_sessions(day, 1)` 等于 `next_session(day)` |
| `count_sessions(start, end)` | `int` | start 到 end 之间的交易日个数，不含 start、含 end；start ≤ end |
| `session_at(t)` | `TradingSession \| None` | 满足 open ≤ t < close 的时段 |
| `next_session_after(t)` | `TradingSession` | open > t 的第一个时段 |
| `last_session_before(t)` | `TradingSession` | close ≤ t 的最后一个时段 |
| `first_session`、`last_session` | `TradingSession` | 覆盖范围的两端（属性） |

为什么这样定：

- 按日期查询也返回 `TradingSession` 而不是 `date`：调用方经常同时需要开盘和收盘时间，要日期时取 `.day`
- "之后"和"之前"都不含当天，n 从 1 开始。Proposal 里的"买入后 5 个交易日"就是 `add_sessions(买入日, 5)`；从节假日开始数时，之后的第一个交易日算第 1 个
- 计数不含起点、含终点，正好是 `add_sessions` 的逆运算，用 Hypothesis 测试
- 常规时段按左闭右开：恰好在收盘时刻提交的订单不算在常规时段内
  - `next_session_after` 用严格大于：恰好在开盘那一刻，今天的时段已经开始，所以"下一个开盘"是下一个交易日
  - `last_session_before` 用小于等于：恰好在收盘那一刻，今天的时段已经收盘
  - 三个方法合起来，任意时刻要么在某个时段内，要么在两个相邻时段之间，没有空隙也没有重叠
- **覆盖范围由交易时段本身推出**，不另外传入：
  - 日期必须在 `[first.day, last.day]` 内
  - 时刻必须在 `[first.open, last.close)` 内
  - 答案也必须在数据范围内
  - 这几条检查只是比较下标，但必须有：不检查的话，`previous_session(第一个交易日)` 会因为下标变成 -1 返回最后一个交易日，范围外的日期也会被当成休市，都是不报错的错误答案
- 实现方式：保存三个平行的元组（交易日、开盘、收盘），用 `bisect` 查找，O(log n)；`add_sessions` 直接用下标相加。默认范围大约 2200 个交易日

后续变更大致这样使用（只是示例，本次不实现）：

| Proposal 里的说法 | 用法 |
| --- | --- |
| 是否在常规交易时段 | `session_at(now) is not None` |
| 方案有效期覆盖到下一个开盘 | `next_session_after(now).open` |
| 收盘后的对账、心跳针对哪个交易日 | `last_session_before(now)` |
| 盘中跌幅的基准（上一交易日收盘价） | `previous_session(当前时段.day)` |
| 复查日、跟进时间、收益窗口 | `add_sessions`、`count_sessions` |
| 顺延到恢复后的第一个交易日 | 降级变更在 `session_at` 和 `next_session_after` 之间选择 |

- 考虑过：统一规定"某个时刻属于哪个交易日"，比如收盘后算下一个交易日
  - 没有采用：当日盈亏按交易时段算，每日预算可能按美东自然日算，答案不同。日历只提供基本查询，各规则自己选

### D5 纽交所日历的加载

`integrations/nyse_calendar.py` 提供：

- `load_nyse_calendar(start: date, end: date) -> TradingCalendar`
- 默认加载范围的两个常量：`DEFAULT_FIRST_DAY = date(2020, 1, 1)`、`DEFAULT_LOOKAHEAD = timedelta(days=730)`。M2 由 bootstrap 用它们和启动时刻算出范围

加载步骤：

1. 用 `exchange_calendars.get_calendar("XNYS", start=..., end=...)` 读取。起止日期一律显式传入，结果就不受加载时刻影响
2. 从 `schedule` 逐行读出交易日、开盘和收盘，用 `to_pydatetime().astimezone(UTC)` 统一换算成 `datetime.UTC`
3. 用结果构造 `TradingCalendar`

起止日期不合理时（起点不早于终点、范围内没有交易日），直接抛出日历库自己的错误，不另外处理。XNYS 的交易日和常规时段同样适用于纳斯达克上市的股票。

- 考虑过：以 moomoo 的 `request_trading_days` 为主数据源
  - 没有采用：它只能查到当年 12 月 31 日，跟进最远可以排到 60 个交易日以后；它不区分半日市；而且要连着 OpenD 才能用。M2 用它做每日核对
- 考虑过：`pandas_market_calendars`
  - 没有采用：它对大多数交易所只是包了一层 exchange_calendars，本项目只需要交易时段

### D6 依赖和类型检查

- 用 `uv add exchange-calendars` 加入运行依赖，写作时的版本是 4.13.2。它会带入 pandas 3.x、numpy、tzdata、pyluach、korean_lunar_calendar、toolz
- 只有 `integrations/nyse_calendar.py` import 它。本项目的代码和测试都不直接 import pandas，所以不单独声明 pandas
- mypy：在 `pyproject.toml` 加 `[[tool.mypy.overrides]]`，`module = ["exchange_calendars", "exchange_calendars.*"]`，设 `follow_untyped_imports = true`
  - 没有采用 `ignore_missing_imports`：那样整个模块都按 `Any` 处理。`follow_untyped_imports` 会使用日历库自己的类型标注，能发现属性名写错这类问题

### D7 放行系统时钟读取系统时间

`pyproject.toml` 的 `per-file-ignores` 增加 `"src/tradingagentspro/integrations/system_clock.py" = ["TID251"]`。和 M0 验证 Ruff 规则的做法一样，实现时用临时探针手工确认：别的文件读取系统时间仍然报错。

### D8 测试

- 新增测试目录 `tests/shared/` 和 `tests/integrations/`，镜像 `src/` 的结构，各放一个 `__init__.py`
- **样例日历** `tests/fakes/calendar.py`：
  - `sample_nyse_calendar(start=date(2026, 9, 1), end=date(2027, 1, 29))` 返回这段时间的纽交所日历
  - 构造方式：取工作日，去掉 2026-09-07、2026-11-26、2026-12-25、2027-01-01、2027-01-18 这几个休市日；2026-11-27 和 2026-12-24 在美东 13:00 收盘；其余按美东 09:30 到 16:00。美东时间用测试代码里的 `zoneinfo` 换算成 UTC
  - shared 的测试，以及以后各模块 domain 的测试都用它，不需要日历库。spec 里"纽交所日历"的场景用默认范围；覆盖范围的场景用 2026-10-01 到 2026-12-31 这一段
  - `tests/integrations/test_nyse_calendar.py` 断言样例日历和加载器在同一范围内的结果完全相同，所以样例数据不会和真实数据不一致
- **Hypothesis** 生成任意的交易时段数据：从一段日期里随机挑选若干个不重复的日期并排序，每个时段的开盘和收盘随机落在当天的 UTC 范围内，开盘早于收盘。验证 spec 里的两条性质：推算和计数互逆；任意时刻要么在某个时段内，要么在两个相邻时段之间
- FakeClock 没有逻辑，不单独测试
- 每条 spec 场景都对应至少一个测试，测试名里写明场景

### D9 文档和约定

- `shared/__init__.py` 的说明把"交易日历协议"改为"交易日历"
- `openspec/config.yaml` 的 `context`：
  - "domain 可以接收 shared 中纯计算、无 I/O 的协议（比如交易日历）"改为"对象或协议（比如交易日历）"
  - 补充一条：交易日和交易时段一律通过 shared 的交易日历计算，不把 UTC 时刻的日期部分当作交易日
  - "读取时间和等待都通过 Clock"改为"读取时间通过 Clock，等待用 DBOS 的持久化休眠"，Ruff 禁用 `time` 模块的提示同样修改，因为 Clock 不提供等待（D1）

## Risks / Trade-offs

- [临时休市在日历库收录之前，系统不知道] → 过去 25 年只有约 6 次；漏掉时当天的订单被券商拒绝、心跳提前一天，后果有限；M2 和 moomoo 每日核对时会发现，届时升级日历库
- [加载范围用完后查询报错] → 默认往后 730 天，重启时重新加载
- [升级日历库后结果变化] → 版本由 `uv.lock` 锁定；加载测试固定了 2025–2027 年的公告事实，升级时重跑就能发现变化
- [pandas 和 numpy 体积大] → 只有加载文件 import 它们；shared 和 domain 的测试用样例日历，不加载它们
- [对 UTC 时刻调用 `.date()` 当作交易日] → spec 里有"UTC 日期和美东日期不是同一天"的场景；`config.yaml` 写明约定，再靠代码评审

## Migration Plan

只新增代码和一个运行依赖，不涉及部署和数据迁移；需要回滚时直接 revert。
