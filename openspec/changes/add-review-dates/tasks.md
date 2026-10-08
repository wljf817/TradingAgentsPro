# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [ ] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 14 条修改完整版 Proposal，每处只加或改一两句话：
  - 「触发」开头"复查类案件续期"一段：催化剂到期后续期要写新的催化剂，只改截止日期或只关联事件不算（第 10 条）
  - 「触发 · 2. 定时心跳」持仓复查日："没写就用默认值 5 个交易日"改为必须写、一般设在 5 个交易日后（第 3 条）；买入和续期时在 3 到 20 个交易日之间，之后提前最早到下一个交易日，推后要在结论的理由里说明、不晚于上次买入或续期之后 20 个交易日，加仓不重新起算（第 1、4、13、14 条）；亏损超过 5%（可配置）时平时调整只能提前，续期时复查日不晚于 5 个交易日后（第 8、9 条）；必须是交易日（第 5 条）
  - 同一节催化剂截止日期：每份论点一个催化剂，写最近的那一个（第 2 条）；"事件后一两天"改为日程项之后第 1 到第 2 个交易日（第 6 条）；没有固定日期的不晚于 60 个交易日以后（第 7 条）；平时调整可以改没到期的催化剂，关联事件 ID 时描述和截止日期不变（第 11、12 条）；必须是交易日（第 5 条）
  - 「持仓与退出」持仓周期："复查日默认买入后 5 个交易日"改为"复查日一般设在买入后 5 个交易日"（第 3 条）

  验证：四处和 proposal 的说法一致；`git diff` 里完整版 Proposal 没有其他改动

## 2. 模块骨架和配置

- [ ] 2.1 先写测试 `tests/modules/heartbeat/test_config.py`，每个场景一个测试：spec「复查日与催化剂的配置」的「默认配置」「配置不合法」；另外检查交易日个数取 1、最短 = 默认 = 最长都合法。验证：运行这个测试文件，确认因为 `modules/heartbeat` 不存在而失败
- [ ] 2.2 新建 `modules/heartbeat/`：`__init__.py`（docstring 和导出）、`domain/__init__.py`（只放 docstring）、`config.py` 的 `HeartbeatConfig`（design D6）。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. 输入和复查日

- [ ] 3.1 先写测试：
  - `tests/modules/heartbeat/builders.py`：spec「检查论点里的日期」里的基准数据，"复查日改为""截止日期改为"等辅助函数（design D9）
  - `tests/modules/heartbeat/domain/test_dates.py`，每个场景一个测试，没写种类的场景对两种论点参数化：「检查论点里的日期」两个场景；「新设和续期的复查日」四个场景；「平时调整的复查日」五个场景；「亏损时不能推后复查日」四个场景；「检查的顺序」的「先比较范围再查交易日」的复查日部分

  验证：运行这个测试文件，确认因为 `domain/dates.py` 不存在而失败
- [ ] 3.2 实现 `domain/dates.py` 的 `Thesis`、`ThesisUpdate`、`ThesisDatesRejection`、`thesis_dates_rejection`、`_review_day_rejection`、`_in_loss`（design D2、D3）；催化剂的检查先不写，`_catalyst_rejection` 留到第 4 组。从 `__init__.py` 导出（测试只从包根 import）。验证：3.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 4. 催化剂

- [ ] 4.1 先写测试，写在 `test_dates.py`，每个场景一个测试：「催化剂到期以后」五个场景；「催化剂的截止日期」七个场景；「检查的顺序」的「多项不通过时给出第一项」，以及「先比较范围再查交易日」的截止日期部分。验证：运行，确认催化剂的测试因为还没有检查而失败，第 3 组的测试仍然通过
- [ ] 4.2 实现 `_catalyst_rejection`、`_links_event`、`_is_new`、`_deadline_rejection`，接进 `thesis_dates_rejection`（design D4）。验证：4.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 5. 上次复查的那一天和改配置

- [ ] 5.1 先写测试，写在 `test_dates.py`，每个场景一个测试：「上次复查的那一天」三个场景；「复查日与催化剂的配置」的「改配置改变检查」。验证：运行，确认上次复查的那一天的测试因为没有 `last_review_day_after` 而失败
- [ ] 5.2 实现 `last_review_day_after`（design D5），从 `__init__.py` 导出。验证：5.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 性质测试

- [ ] 6.1 写 `tests/modules/heartbeat/domain/test_dates_properties.py`（design D9）：从新设开始随机一串续期和平时调整，只有通过的论点生效，每步以后检查 D9 列出的不变量。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/heartbeat` 通过
  - 用 `event` 确认样例里出现过十种拒绝原因和三种情形
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 7. 整体检查

- [ ] 7.1 对照 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 7.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
