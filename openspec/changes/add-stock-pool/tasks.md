# Tasks

## 1. 完整版 Proposal 补上已决定的三条

- [ ] 1.1 按 proposal「需要人决定」第 1 到 3 条修改完整版 Proposal，每处只加一两句话：
  - 「股票池与寻股」的字段表加一行"板块"：加入时由代码从数据源写入，方案和持仓的板块都取这里，PM 不填；用哪套板块分类跟数据源一起定
  - 「股票池与寻股」的增删规则里，"在允许交易的范围内"后面写明范围：纽交所、纳斯达克上市的普通股（含 ADR），不含 ETF、OTC 和杠杆产品，可配置
  - 「调度器与运行模块」的运行约束加一条：案件侧按天计的上限（每日调用预算、每天增删次数等）按交易日算，一个交易日从上一个交易日收盘到这个交易日收盘，周末和休市日并进下一个交易日

  验证：三处和 proposal 的说法一致；`git diff` 里完整版 Proposal 没有其他改动

## 2. 时刻归属的交易日

- [ ] 2.1 先写测试 `tests/shared/test_trading_calendar.py`：trading-calendar spec「时刻归属的交易日」的五个场景，其中"归属的交易日是所在时段或下一个开盘的时段"用 `tests/shared/strategies.py` 的 `session_lists` 写性质测试，和 `session_at(t) or next_session_after(t)` 对照。验证：运行这个测试文件，确认因为 `session_closing_after` 不存在而失败
- [ ] 2.2 在 `shared/trading_calendar.py` 的 `TradingCalendar` 实现 `session_closing_after`（design D2）。验证：2.1 的测试和已有的日历测试通过；`uv run mypy`、`uv run pytest tests/architecture` 通过

## 3. contracts

- [ ] 3.1 先写测试 `tests/contracts/test_thesis.py`：stock-pool spec「关注论点的格式」的五个场景，基准关注论点和 spec 一致；格式错误断言抛出 `ValidationError`。验证：运行这个测试文件，确认因为 `contracts/thesis.py` 不存在而失败
- [ ] 3.2 新增 `contracts/thesis.py`（`ConditionKind`、`Comparison`、`Condition`、`ReferenceKind`、`CatalystReference`、`Catalyst`、`WatchThesis`）、`contracts/stock_pool.py`（`StockStatus`）、`contracts/listing.py`（`Exchange`、`SecurityType`、`Listing`），写法见 design D1。验证：3.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 4. 模块和配置

- [ ] 4.1 先写测试 `tests/modules/stockpool/test_config.py`：spec「股票池配置」的「默认配置」「配置不合法」两个场景。同时建好 `tests/modules/stockpool/__init__.py`、`tests/modules/stockpool/domain/__init__.py`。验证：运行这个测试文件，确认因为模块不存在而失败
- [ ] 4.2 新建 `src/tradingagentspro/modules/stockpool/`：`__init__.py`（模块说明，导出 `StockPoolConfig`）、`config.py`（`StockPoolConfig`，design D8）、`domain/__init__.py`。验证：4.1 的测试通过；`uv run lint-imports` 和 `uv run pytest tests/architecture` 通过

## 5. 记录和状态变化

- [ ] 5.1 先写测试 `tests/modules/stockpool/domain/test_entry.py`，直接构造记录：
  - spec「状态变化」表里每一行对应的方法：变化后的状态、过期时间、关注论点；没有变的字段（来源、理由、板块、评分）保持不变
  - D3 的不变量：候选有过期时间、没有关注论点；关注有关注论点、没有过期时间；持仓两者都没有
  - 代码发起的变化遇到不对的状态抛 `ValueError`：已移出的记录买入成交；候选、关注清仓
  - 记分：-2 到 +2 通过并更新上次复查时间；3 和 -3 抛 `ValueError`
  - `removed` 记下移出的原因、时刻和理由，`in_pool` 为假

  验证：运行这个测试文件，确认因为 `domain/entry.py` 不存在而失败
- [ ] 5.2 实现 `domain/entry.py`（`PoolSource`、`RemovalKind`、`Removal`、`PoolEntry` 和状态变化的方法，design D3），并从 `__init__.py` 导出（测试只从包根 import）。验证：5.1 的测试通过；`uv run mypy`、`uv run pytest tests/architecture` 通过

## 6. 检查、过期时间、冷却期和当天的增删次数

- [ ] 6.1 先写测试 `tests/modules/stockpool/domain/test_rules.py`，用纽交所样例日历：
  - `candidate_expiry`：spec「过期时间按交易日计算」的三种情况，包括收盘后加入和夏令时结束后的收盘时刻
  - `cooling_down`：spec「冷却期的边界」「收盘后移出从下一个交易日算起」的边界；从来没加入过、没有移出的记录不在冷却期
  - `changes_since`：加入时刻不早于开始时刻的记录计一次，删除时刻不早于开始时刻的再计一次；超期和清仓后移出不计；开始时刻之前的加入和删除不计
  - `add_rejection`：spec「加入候选」表的每一行都有通过和不通过两种情况；流动性门槛等于和低于 0.01 美元；池子容量和次数上限的边界；多项不通过时给出表里靠前的一项
  - `remove_rejection`：spec「删除」表的每一行和顺序
  - `status_rejection`：不在池子里、状态不对、状态对三种情况

  验证：运行这个测试文件，确认因为 `domain/rules.py` 不存在而失败
- [ ] 6.2 实现 `domain/rules.py`（`Rejection` 和 design D4、D5 的函数），并从 `__init__.py` 导出。验证：6.1 的测试通过；`uv run mypy`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 7. 存储和用例

- [ ] 7.1 先写测试：
  - `tests/fakes/stock_pool.py`：`InMemoryStockPoolStore`，按股票代码存在 dict 里（design D6）
  - `tests/modules/stockpool/builders.py`：spec 的基准数据（2026-10-14 15:00Z、AAA/BBB/CCC 三条记录、基准上市信息、基准关注论点、"加入 DDD"的参数）；`stock_pool_service(clock=None, **config)` 用测试时钟、纽交所样例日历、存好三条记录的内存存储和配置新建 `StockPoolService`
  - `tests/modules/stockpool/test_service.py`，PM 和手动发起的操作，每个场景一个测试：
    - 「股票池的记录」四个场景
    - 「状态变化」里的「加入成为候选」「候选升级为关注」「候选保持」「删除候选或关注」「关注降为候选」「状态不允许的操作」「对不在池子里的股票升降级或保持」
    - 「加入候选」七个场景、「删除」三个场景
    - 「重新加入的冷却期」的「冷却期的边界」「收盘后移出从下一个交易日算起」
    - 「池子容量和关注名额」的「池子容量的边界」「关注名额的边界」
    - 「每天的增删次数」的「次数上限的边界」「被拒绝的操作不计入」「收盘后的增删算下一个交易日」「周末并进下一个交易日」
    - 「候选的过期时间」的「过期时间按交易日计算」
    - 「股票池配置」的「允许 ETF」

  验证：运行这个测试文件，确认因为 `ports.py`、`service.py` 不存在而失败
- [ ] 7.2 实现（design D6、D7）：
  - `ports.py`：`StockPoolStore`
  - `service.py`：`StockPoolService` 的 `__init__`、`entry`、`entries`、`add`、`remove`、`promote`、`demote`、`keep`、`record_score`
  - `__init__.py` 增加导出 `StockPoolStore`、`StockPoolService`

  验证：7.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过，module-layers 契约包括新的 service 和 ports
- [ ] 7.3 先写测试，写在 `test_service.py`，代码发起的操作，每个场景一个测试：
  - 「状态变化」里的「候选超期由代码移出」「关注股买入成交变为持仓」「候选买入成交也变为持仓」「加仓成交不改变持仓」「代码触发的清仓转为候选」「PM 清仓后转为关注」「PM 清仓后移出」「代码发起的操作遇到不对的状态」
  - 「重新加入的冷却期」的「超期移出也进入冷却期」「清仓后移出也进入冷却期」
  - 「池子容量和关注名额」的「持仓不占池子容量」「清仓后转为关注可以超出关注名额」「清仓后转为候选可以超出池子容量」
  - 「每天的增删次数」的「不是增删的变化不计入」
  - 「候选的过期时间」的「到期的边界」「错过收盘后的那一次」「保持以后按新的过期时间」「升级以后不再过期」

  验证：运行，确认因为 `expire_candidates`、`bought`、`closed_by_code`、`closed_by_pm` 还没有实现而失败
- [ ] 7.4 实现 `StockPoolService` 的 `expire_candidates`、`bought`、`closed_by_code`、`closed_by_pm`（design D7）。验证：7.3 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 8. 状态机性质测试

- [ ] 8.1 写 `tests/modules/stockpool/test_state_machine.py`（design D10）：Hypothesis 的 `RuleBasedStateMachine` 随机交错全部操作和拨动时间（跨收盘、跨周末），配置用较小的容量、名额和次数上限，让上限经常被碰到；每一步之后检查 D10 列出的不变量。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/stockpool` 通过
  - 用 `event` 确认样例里出现过"池子已满""关注名额已满""增删次数已用完""冷却期内"四种拒绝，以及成交后的状态变化超出上限
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 9. 整体检查

- [ ] 9.1 对照两份 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 9.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1 validate --all --strict --no-interactive`。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
