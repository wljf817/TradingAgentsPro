# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [ ] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 8 条修改完整版 Proposal「触发 · 3. 自定义跟进」，每处只加或改一两句话：
  - 「申请必须写明四项」的原因一条后面：原因类型和引用的对象要对应：事件后反应引用事件，外部日程引用日程项，价格或指标观察引用代码能检测的条件
  - 防止重复的规则表第 2 行："论点里某个催化剂"改为这份结论生效以后的论点里的催化剂，附了论点更新时看更新后的（第 3 条）
  - 「其他规则」的作废一条改为：只有往下走的状态变化让待执行的跟进作废：清仓（不论之后转为关注、候选还是移出）、关注降为候选、移出股票池；关注股买入变成持仓时保留（第 1 条）
  - 「其他规则」的每只股票最多 2 个：替换不算新增，可在配置里改（第 8 条）
  - 「其他规则」的触发时间：必须在现在之后；60 个交易日从今天（现在归属的交易日）算起（第 5 条）
  - 「其他规则」的每天的申请次数：按交易日算，默认 5 次，只算获批的申请，新增和替换都算（第 4 条）
  - 「其他规则」加一条：申请被拒绝时和结论校验不通过一样，带着原因退回 PM 修改（第 2 条）
  - 「其他规则」加一条：跟进只触发一次，跟进案件没有结论时不重新触发；到点时处于降级模式或预算用完照常入队（第 6 条）

  验证：各处和 proposal 的说法一致；`git diff` 里完整版 Proposal 只有这一节有改动

## 2. 模块和配置

- [ ] 2.1 先写测试 `tests/modules/followups/test_config.py`：spec「跟进的配置」的「默认配置」「配置不合法」两个场景；另外检查三项取 1 都合法。同时建好 `tests/modules/followups/__init__.py`、`tests/modules/followups/domain/__init__.py`。验证：运行这个测试文件，确认因为模块不存在而失败
- [ ] 2.2 新建 `src/tradingagentspro/modules/followups/`：`__init__.py`（模块说明，导出 `FollowUpConfig`）、`config.py`（`FollowUpConfig`，design D10）、`domain/__init__.py`。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. 跟进的记录和状态

- [ ] 3.1 先写测试 `tests/modules/followups/domain/test_follow_up.py`，直接构造记录：
  - 新的记录是待执行
  - `triggered`、`replaced`、`voided` 各自变为已触发、被替换、已作废，其他字段不变
  - spec「跟进的状态」表的最后一行：已触发、被替换、已作废的记录再调用三个方法中的任何一个都抛 `ValueError`

  验证：运行这个测试文件，确认因为 `domain/follow_up.py` 不存在而失败
- [ ] 3.2 实现 `domain/follow_up.py`（`FollowUpStatus`、`FollowUp` 和状态变化的方法，design D2），并从 `__init__.py` 导出（测试只从包根 import）。验证：3.1 的测试通过；`uv run mypy`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 4. 审批和作废的规则

- [ ] 4.1 先写测试 `tests/modules/followups/domain/test_rules.py`，用纽交所样例日历：
  - `request_rejection`：spec「审批的顺序」表的每一行都有通过和不通过两种情况；「引用的对象」表的三种对应和四种不对应；和催化剂比较时种类不同、催化剂不引用、引用条件都不算重复；触发时间等于现在、晚 1 秒；上限前 1 分钟、正好在上限；每只股票的上限和每天的上限的边界；有原因相同的待执行跟进时不受每只股票的上限限制；多项不通过时给出表里靠前的一项
  - `trigger_limit`：spec「最远间隔的边界」「收盘后审批从下一个交易日算起」的上限，包括夏令时结束以后的收盘时刻和周末审批
  - `same_reason`：原因类型和引用都相同才算；日程项和事件 ID 相同、种类不同不算；条件的值不同不算；没有时为 None
  - `due`：触发时间等于现在的给出、晚 1 秒的不给；按触发时间、再按获批的先后排列
  - `voids_follow_ups`：spec「股票状态往下走时作废」表的每一行，以及之前和之后相同（加仓成交）

  验证：运行这个测试文件，确认因为 `domain/rules.py` 不存在而失败
- [ ] 4.2 实现 `domain/rules.py`（`FollowUpRejection` 和 design D3、D4、D6、D7 的函数），并从 `__init__.py` 导出。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run pytest tests/architecture` 通过

## 5. 存储和审批

- [ ] 5.1 先写测试：
  - `tests/fakes/follow_ups.py`：`InMemoryFollowUpStore`，按跟进 ID 存在 dict 里（design D8）
  - `tests/modules/followups/builders.py`：spec「跟进的记录」里的基准数据（2026-10-14 15:00Z、日程申请、事件申请、观察申请、CCC 和 BBB 的催化剂、来源案件 K-1 和 K-2）；`follow_up_service(clock=None, **config)` 用测试时钟、`FakeIds`、纽交所样例日历、空的内存存储和配置新建 `FollowUpService`（design D13）
  - `tests/modules/followups/test_service.py`，审批和查询，每个场景一个测试：
    - 「跟进的记录」两个场景
    - 「审批的输入和结果」两个场景、「审批的顺序」一个场景
    - 「引用的对象」三个场景、「不和催化剂重复」三个场景、「触发时间」三个场景
    - 「原因相同时替换」四个场景
    - 「每只股票的待执行跟进上限」的「上限的边界」「达到上限时仍可以替换」
    - 「每天的申请次数」的「次数上限的边界」「替换也计入」「被拒绝的申请不计入」「收盘后的申请算下一个交易日」「周末并进下一个交易日」
    - 「跟进的配置」的「改配置改变审批」

  验证：运行这个测试文件，确认因为 `ports.py`、`service.py` 不存在而失败
- [ ] 5.2 实现（design D5、D8、D9）：
  - `ports.py`：`FollowUpStore`
  - `service.py`：`FollowUpService` 的 `__init__`、`pending`、`request` 和 `_approved_today`
  - `__init__.py` 增加导出 `FollowUpStore`、`FollowUpService`，docstring 写明模块的用途和 follow-ups spec

  验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过，module-layers 契约包括新的 service 和 ports

## 6. 到点触发和作废

- [ ] 6.1 先写测试，写在 `test_service.py`，每个场景一个测试：
  - 「跟进的状态」四个场景
  - 「每只股票的待执行跟进上限」的「触发以后腾出名额」
  - 「每天的申请次数」的「触发和作废不退还次数」
  - 「到点触发」四个场景
  - 「股票状态往下走时作废」五个场景

  验证：运行，确认因为 `trigger_due`、`status_changed` 还没有实现而失败，第 5 组的测试仍然通过
- [ ] 6.2 实现 `FollowUpService` 的 `trigger_due`、`status_changed`（design D6、D7、D9）。验证：6.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 7. 状态机性质测试

- [ ] 7.1 写 `tests/modules/followups/test_state_machine.py`（design D13）：Hypothesis 的 `RuleBasedStateMachine` 随机交错申请、拨动时间（跨收盘、跨周末）、到点触发和状态变化，配置用较小的上限；每一步之后检查 D13 列出的不变量。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/followups` 通过
  - 用 `event` 确认样例里出现过七种拒绝原因、替换、到点触发和状态往下走时作废
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 8. 整体检查

- [ ] 8.1 对照 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 8.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
