# Tasks

## 1. 依赖与类型检查

- [x] 1.1 加入运行依赖并配置 mypy（design D6）：
  - 运行 `uv add exchange-calendars`
  - 在 `pyproject.toml` 加 `[[tool.mypy.overrides]]`：`module = ["exchange_calendars", "exchange_calendars.*"]`，`follow_untyped_imports = true`

  验证：
  - `uv sync --locked` 成功，`uv run python -c "import exchange_calendars"` 不报错
  - 临时建探针 `src/tradingagentspro/integrations/_probe.py`，只写 `import exchange_calendars`。去掉 override 时 `uv run mypy` 报 `import-untyped`，加上后通过。确认后删除探针
  - `uv run lint-imports` 和 `uv run pytest` 仍然通过

## 2. 时钟

- [x] 2.1 先写测试：
  - 建 `tests/integrations/__init__.py`
  - `tests/integrations/test_system_clock.py`：覆盖 spec「系统时钟在 UTC+14 的进程里返回 UTC 时间」；写一行 `clock: Clock = SystemClock()`，让 mypy 检查它满足协议

  验证：运行，确认因为 `shared/clock.py` 和 `integrations/system_clock.py` 还不存在而失败
- [x] 2.2 实现（design D1、D7）：
  - `shared/clock.py`：Clock 协议，只有 `now()`。docstring 写明返回带时区的 UTC 时间，并且不保证递增
  - `integrations/system_clock.py`：SystemClock
  - `pyproject.toml` 的 `per-file-ignores` 只为 `integrations/system_clock.py` 放行 `TID251`
  - `tests/fakes/clock.py`：FakeClock，只有 `now`、`set`、`advance`

  验证：
  - 2.1 的测试通过
  - 临时在 `integrations/` 下建探针文件调用 `datetime.now(UTC)`，`uv run ruff check` 报 TID251；确认后删除探针
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过，`tests/architecture` 对真实包的检查通过

## 3. 日历的构造

- [x] 3.1 先写测试：
  - 建 `tests/shared/__init__.py`
  - `tests/shared/test_trading_calendar.py`：用三个手写的交易时段构造日历，断言 `first_session` 和 `last_session`

  验证：运行，确认因为 `shared/trading_calendar.py` 不存在而失败
- [x] 3.2 在 `shared/trading_calendar.py` 实现 `TradingSession`、`CalendarRangeError`，以及 `TradingCalendar` 的构造和 `first_session`、`last_session`（design D3）。验证：
  - 3.1 的测试通过
  - `uv run mypy`、`uv run ruff check` 通过
  - 架构测试对真实包通过（纯区域的 import 白名单和禁用的内置函数）
- [x] 3.3 写样例日历 `tests/fakes/calendar.py` 的 `sample_nyse_calendar(start, end)`（design D8），并用它补上 spec「覆盖范围是第一个到最后一个交易日」的测试。验证：测试通过，`uv run mypy` 通过
- [x] 3.4 更新文档（design D9）：
  - `shared/__init__.py` 的说明把"交易日历协议"改为"交易日历"
  - `openspec/config.yaml` 的 `context`：把"domain 可以接收 shared 中纯计算、无 I/O 的协议（比如交易日历）"改为"对象或协议"；补充一条：交易日和交易时段一律通过 shared 的交易日历计算，不把 UTC 时刻的日期部分当作交易日
  - 保留工作区里 `config.yaml` 已有的其他改动

  验证：
  - `openspec instructions design --change add-trading-calendar-and-clock --json` 返回的 `context` 里有新的约定
  - 架构测试"层级包的 `__init__.py` 只有 docstring"通过

## 4. 按日期查询和推算

- [x] 4.1 先写测试，写在 `tests/shared/test_trading_calendar.py`：
  - spec「按日期判断交易日」和「按日期推算交易日」的全部场景，用样例日历
  - spec「超出覆盖范围的查询报错」里的「日期超出覆盖范围」和「推算结果超出数据范围」
  - Hypothesis 性质测试「推算和计数互逆」。生成交易时段数据的策略放在 `tests/shared/strategies.py`，第 5 组也要用

  验证：运行，确认因为查询方法还不存在而失败
- [x] 4.2 实现 `is_session`、`session`、`next_session`、`previous_session`、`add_sessions`、`count_sessions`（design D4），用 `bisect` 和下标运算，并检查覆盖范围。验证：
  - 4.1 的测试通过
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/shared` 通过
  - `uv run mypy` 通过

## 5. 按时刻查询

- [x] 5.1 先写测试，写在 `tests/shared/test_trading_calendar.py`：
  - spec「按时刻判断交易时段」的全部场景，用样例日历
  - spec「超出覆盖范围的查询报错」里的「时刻或它的答案超出数据范围」

  验证：运行，确认因为方法还不存在而失败
- [x] 5.2 实现 `session_at`、`next_session_after`、`last_session_before`（design D4），按左闭右开计算，并检查覆盖范围。验证：
  - 5.1 的测试通过
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/shared` 通过
  - `uv run mypy` 通过

## 6. 加载纽交所的交易时段

- [x] 6.1 先写测试 `tests/integrations/test_nyse_calendar.py`：
  - spec「加载纽交所的交易时段」的全部场景。测试进程本来就在 UTC+14，"进程时区不影响结果"由时区守卫覆盖
  - 样例日历和加载器在 2026-09-01 到 2027-01-29 的结果完全相同（design D8），逐日比较 `session(day)`

  验证：运行，确认因为 `integrations/nyse_calendar.py` 不存在而失败
- [x] 6.2 实现 `integrations/nyse_calendar.py`（design D5）：`load_nyse_calendar`，以及 `DEFAULT_FIRST_DAY`、`DEFAULT_LOOKAHEAD` 两个常量。起止日期显式传给日历库；时间用 `to_pydatetime().astimezone(UTC)` 换算。验证：
  - 6.1 的测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
  - `uv run pytest tests/architecture` 通过，其中包括"`astimezone` 必须带参数"的规则

## 7. 整体检查

- [x] 7.1 按 CI 的顺序在本地完整跑一遍：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1 validate --all --strict --no-interactive`。验证：
  - 全部通过
  - 逐条核对两个 spec 的每个场景都有对应的测试
  - `git status` 里没有探针文件和缓存目录残留
