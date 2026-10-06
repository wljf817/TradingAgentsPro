# Tasks

## 1. 完整版 Proposal

- [x] 1.1 按 proposal「需要人决定」第 1 条（人已经同意）修改完整版 Proposal：「订单生命周期」表的"部分成交""加仓"两行、「技术栈与部署」表的"券商"一行，在原文后加上那里写的文字。验证：三处和 proposal 写的一致，两张表的其他行没有改动

## 2. 配置项

- [ ] 2.1 先写测试 `tests/modules/execution/test_config.py`：order-lifecycle spec「执行配置」的「默认配置」（买单和止损单错开关闭）和「打开买单和止损单错开」。验证：运行这个测试文件，确认因为配置项不存在而失败
- [ ] 2.2 实现 `ExecutionConfig.stagger_buys_and_stops`，默认 False（design D1）。验证：2.1 的测试和全部已有测试通过；`uv run mypy` 通过

## 3. 止损目标和错开中

- [ ] 3.1 先写测试 `tests/modules/execution/domain/test_protection.py`（design D2、D4、D5）：
  - `stop_target(..., stagger=True)`：有待提交、已提交、部分成交、状态未知的买单时股数为 0，止损价和未覆盖的股数照常；买单都已终态时和不错开一样；`stagger=False` 时和原来一样
  - `StopTarget.staggered`：只在因为错开改为 0、不错开时大于 0 时为真；持仓为 0 的新买入、卖单让出全部股数时为假
  - spec「止损目标」的两个新场景
  - `stop_withdrawn`：没有止损单、已经结束、还没挂出去为真；挂着、状态未知为假
  - `Protection.staggered` 默认为假，`with_staggered` 只改这一项
  - `backup_stop_due`：错开中和无保护仓位一样触发，其他三个条件不变；两者都不是时不触发

  验证：运行这个测试文件，确认因为参数、字段和函数不存在而失败
- [ ] 3.2 实现 `domain/protection.py` 的改动：`stop_target` 的 `stagger` 参数、`StopTarget.staggered`、`Protection.staggered` 和 `with_staggered`、`stop_withdrawn`、`backup_stop_due` 加上错开中；从包根导出 `stop_withdrawn`。验证：3.1 的测试和全部已有测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 4. 执行用例

- [ ] 4.1 先写测试 `tests/modules/execution/test_stagger_service.py`。和其他执行测试文件一样，自己建一套执行测试环境（存储、假券商、假止损单接口、测试时钟，新建后先对账一次），配置打开买单和止损单错开（「开关关闭时加仓不撤止损单」用默认配置）。每个 spec 场景一个测试，测试名或说明里写明场景：
  - stop-protection spec「买单和止损单错开」九个场景
  - stop-protection spec「本地备用止损的触发」的「错开中、价格跌到止损价时触发」「错开结束、挂上止损单后不触发」
  - stop-protection spec「保存止损保护」的「重启后仍是错开中」
  - order-lifecycle spec「先查询再提交」的「买单和止损单错开时，止损单还没撤掉不提交买单」

  验证：运行这个测试文件，确认失败的是开关打开时的场景，原因是买单没有先等止损单撤掉、止损目标没有错开
- [ ] 4.2 实现 design D3、D4：`_track` 在开关打开时对还没提交出去、没有撤单要求的买单也先对齐，用 `stop_withdrawn` 决定能不能提交；`_align` 把开关传给 `stop_target`，在 `_act` 之后按目标记下错开中。验证：
  - 4.1 的测试和全部已有测试通过，包括「先查询再提交」「卖单先让出股数」「提前撤单」原有的场景
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
- [ ] 4.3 `tests/modules/execution/test_repo.py`：错开中的止损保护存进数据库再读出来不变；用 `TypeAdapter(Protection)` 读一条去掉 `staggered` 的旧记录，得到不是错开中。验证：开着 Docker 运行这个测试文件，通过

## 5. 性质测试

- [ ] 5.1 `tests/modules/execution/test_protection_service.py` 增加 `StaggeredStopMachine`（design D7）：继承 `StopProtectionMachine`，配置打开开关；增加"持有时提交加仓"和"加仓订单成交"两步；增加不变式"券商那边 BBB 不会同时有挂着的买单和挂着的止损单"；结束时按打开开关算的止损目标检查止损单，并检查错开中和"有买单没结束、持仓大于 0"一致。验证：`uv run pytest tests/modules/execution/test_protection_service.py` 通过，原来的状态机也通过，并且确认样例里出现过加仓提交给券商、错开以后挂回止损单（用 `event` 统计）

## 6. 收尾

- [ ] 6.1 对照 delta spec，确认每个新增场景都有对应的测试；修改过的五条要求，原有场景的测试仍然通过。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
