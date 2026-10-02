# Tasks

## 1. 依赖和约定

- [x] 1.1 加入 Pydantic 并配置 mypy（design D3）：
  - 运行 `uv add pydantic`
  - `pyproject.toml` 的 `[tool.mypy]` 加 `plugins = ["pydantic.mypy"]`，新增 `[tool.pydantic-mypy]`：`init_forbid_extra`、`init_typed`、`warn_required_dynamic_aliases` 都设为 `true`

  验证：
  - `uv sync --locked` 成功
  - 临时建探针 `src/tradingagentspro/contracts/_probe.py`：定义一个带 `Decimal` 字段的 Pydantic 模型，并用 float 构造它。`uv run mypy` 报 `arg-type`；确认后删除探针
  - `uv run lint-imports` 和 `uv run pytest` 仍然通过
- [x] 1.2 修改数值类型的约定（design D3）：
  - `openspec/config.yaml` 的 `context`："金额、价格、数量用 Decimal"改为"金额、价格和比例用 Decimal，股数用 int（只交易整股）"
  - `shared/__init__.py` 的说明去掉"Decimal 数值类型"

  验证：
  - `openspec instructions design --change add-risk-rules --json` 返回的 `context` 里是新的约定
  - 架构测试"层级包的 `__init__.py` 只有 docstring"通过

## 2. 跨模块的模型

- [x] 2.1 先写测试：
  - 建 `tests/contracts/__init__.py`
  - `tests/contracts/test_trade_plan.py`：覆盖 spec「交易方案的格式」的全部场景，格式错误断言抛出 `ValidationError`

  验证：运行，确认因为 `contracts/trade_plan.py` 不存在而失败
- [x] 2.2 实现 contracts（design D2）：
  - `contracts/trade_plan.py`：`TradeAction`（带 `is_opening`）、`OrderType`、`TradePlan`；按动作区分的要求用 `model_validator(mode="after")` 检查
  - `contracts/account.py`：`Position`、`AccountSnapshot`，不加约束
  - `contracts/risk.py`：`RiskOutcome`、`RiskViolation`、`RiskDecision`
  - 所有模型设 `frozen=True, extra="forbid"`

  验证：
  - 2.1 的测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
  - `uv run pytest tests/architecture` 通过（contracts 只用了 pydantic 和白名单里的标准库）

## 3. 配置、输入和计算

- [x] 3.1 先写测试：
  - 建 `tests/modules/__init__.py`、`tests/modules/risk/__init__.py`、`tests/modules/risk/domain/__init__.py`
  - `tests/modules/risk/builders.py`：spec 的基准数据，提供 `baseline_account()`、`baseline_plan(**changes)`、`baseline_request(**changes)`，交易日历用样例日历（design D9）
  - `tests/modules/risk/test_config.py`：spec「不提供配置时使用默认值」
  - `tests/modules/risk/domain/test_exposure.py`：
    - spec「计划风险和市值的算法」的全部场景
    - 执行交易日：盘中是当天，盘后是下一个开盘的交易日
    - 可卖数量扣除卖出预留，没有持仓时为 0
    - 生效的紧急阈值：没填时是默认值

  验证：运行，确认因为 `modules/risk` 不存在而失败
- [x] 3.2 实现（design D1、D4、D8）：
  - `modules/risk/config.py`：`RiskConfig`，默认值按 spec 的表，每项只加"大于 0"的约束
  - `modules/risk/domain/request.py`：`OpeningReservation`、`SellReservation`、`RiskRequest`（frozen dataclass）
  - `modules/risk/domain/exposure.py`：计划风险、合并后的计划风险、持仓市值、板块市值、可卖数量、执行交易日、生效的紧急阈值
  - `modules/risk/__init__.py`：模块说明，导出 `RiskConfig`、`RiskRequest`、`OpeningReservation`、`SellReservation`

  验证：
  - 3.1 的测试通过
  - `uv run mypy`、`uv run ruff check` 通过
  - `uv run lint-imports` 通过，其中包括 module-layers 契约
  - `uv run pytest tests/architecture` 通过（domain 没有用第三方包和禁用的内置名字）

## 4. 规则接口和判定

- [x] 4.1 先写测试 `tests/modules/risk/domain/test_engine.py`，规则用测试里写的替身（比如"股数不超过 40"、"总是不通过"）：
  - 通过：请求股数通过时，结论为通过，股数不变，原因为空
  - 调整：结论为调整，股数是能通过的最大股数，原因是请求股数下的违规
  - 拒绝：1 股就不通过时结论为拒绝，没有股数，原因是 1 股时的全部违规
  - 清仓的请求股数是可卖数量
  - 生效的紧急阈值只在开仓类方案通过或调整时才有
  - Hypothesis：随机生成"股数不超过 k"一类的替身规则，结论为调整时，最终股数通过、多 1 股不通过

  验证：运行，确认因为 `domain/rules.py` 和 `domain/engine.py` 不存在而失败
- [x] 4.2 实现（design D5）：
  - `domain/rules.py`：`RiskRule` 协议，`OPENING_RULES` 和 `EXIT_RULES` 先是空元组
  - `domain/engine.py`：`decide(request, rules, requested)` 实现判定和二分查找；`evaluate_plan(request)` 按动作选规则组、确定请求股数，再调用 `decide`
  - `modules/risk/__init__.py` 增加导出 `evaluate_plan`

  验证：
  - 4.1 的测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 开仓类规则

- [x] 5.1 先写测试：
  - `tests/modules/risk/domain/strategies.py`：随机生成 `RiskRequest`，包括 0 到 4 只持仓的账户、四种动作的合法方案、开仓预留和卖出预留、熔断状态、样例日历范围内的当前时刻和最近成交日（design D9）
  - `test_rules.py` 的性质测试：spec「每条已登记的规则都满足单调性」，遍历 `OPENING_RULES` 和 `EXIT_RULES`
  - `test_engine.py` 的性质测试：用已登记的规则检查 spec「调整后的股数是能通过的最大股数」，以及"结论为通过时请求股数通过全部规则，结论为拒绝时 1 股就不通过"
  - `test_rules.py` 的场景测试，调用 `evaluate_plan`：
    - spec「通过」
    - 开仓类规则表前五行（已结算资金、单只仓位上限、单笔计划风险、组合合计风险、板块集中度）的全部场景
    - spec「预留的资金从已结算资金中扣除」「预留的风险计入组合合计风险」「预留的市值计入板块」
    - spec「多条规则限制股数时取最小」「1 股都通不过时拒绝」「修改阈值改变结果」

  验证：性质测试通过（规则组还是空的）；场景测试因为规则还没有实现而失败
- [x] 5.2 实现这五条规则并登记到 `OPENING_RULES`（spec「开仓类规则」、design D4、D5），计算一律调用 `exposure.py`。验证：
  - 5.1 的测试通过
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/risk` 通过
  - `uv run mypy`、`uv run ruff check` 通过
- [x] 5.3 先写测试，写在 `test_rules.py`，调用 `evaluate_plan`：
  - 开仓类规则表后六行（最多持仓数、未完成的开仓方案、最短交易间隔、止损价、紧急阈值、单日亏损熔断）的全部场景，包括「当前时刻超出交易日历的覆盖范围」
  - spec「有开仓预留的股票占用持仓名额」
  - spec「拒绝时列出全部不通过的规则」「按实际持仓判断新建仓还是加仓」

  验证：运行，确认因为这六条规则还没有实现而失败
- [x] 5.4 实现这六条规则并登记到 `OPENING_RULES`。最短交易间隔用 `exposure.py` 的执行交易日和 `calendar.count_sessions`；紧急阈值用生效的紧急阈值。验证：
  - 5.3 和之前的测试都通过
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/risk` 通过，单调性检查已经包括这六条
  - `uv run mypy`、`uv run ruff check` 通过

## 6. 减仓类规则

- [x] 6.1 先写测试，写在 `test_rules.py`，调用 `evaluate_plan`：spec「减仓类规则」的全部场景。验证：运行，确认因为规则还没有实现而失败
- [x] 6.2 实现"可卖数量"规则并登记到 `EXIT_RULES`（design D7：不用券商的可卖数量，扣除卖出预留）。验证：
  - 6.1 和之前的测试都通过
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/risk` 通过
  - `uv run mypy`、`uv run ruff check` 通过

## 7. 整体检查

- [x] 7.1 按 CI 的顺序在本地完整跑一遍：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1 validate --all --strict --no-interactive`。验证：
  - 全部通过
  - 逐条核对 spec 的每个场景都有对应的测试
  - `git status` 里没有探针文件和缓存目录残留
