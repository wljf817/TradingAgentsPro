# Tasks

## 1. 方案的新字段

- [x] 1.1 先写测试 `tests/contracts/test_trade_plan.py`：
  - risk-rules spec「合法的开仓方案和退出方案」改为四个方案：加仓方案带有效期和允许价格偏离，清仓方案带有效期
  - 新场景「有效期没有时区」「退出方案写了允许价格偏离」「允许价格偏离的格式边界」

  验证：运行 `uv run pytest tests/contracts`，确认因为 `TradePlan` 不接受新字段而失败
- [x] 1.2 `contracts/trade_plan.py`：`TradePlan` 增加 `expires_at: AwareDatetime | None` 和 `max_price_deviation: Decimal | None`（大于 0、小于 1）；`_check_fields_for_action` 拒绝带允许价格偏离的减仓、清仓方案；更新类的说明（design D1）。验证：
  - 1.1 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 2. 首次检查：生效的有效期和「允许价格偏离」规则

- [x] 2.1 先写测试：
  - `tests/modules/risk/test_config.py`：三个新阈值的默认值 0.01、0.10、0.03
  - `tests/modules/risk/domain/test_exposure.py`：
    - `effective_expiry`：方案写了就用方案的；盘中是当天收盘 2026-10-14 20:00Z；盘后 21:00Z 是 2026-10-15 20:00Z
    - `effective_price_deviation`：方案写了就用方案的，没写时是默认值
  - `tests/modules/risk/domain/test_rules.py`：spec「允许价格偏离在范围两端时通过」「允许价格偏离超出范围时拒绝」
  - `tests/modules/risk/domain/test_engine.py`：spec「通过」带上生效的有效期；新场景「生效的有效期」；「1 股都通不过时拒绝」没有生效的有效期
  - `tests/modules/risk/domain/test_reservation.py`：直接构造的 `RiskDecision` 补上 `expires_at`
  - `tests/modules/risk/domain/strategies.py`：开仓方案随机带允许价格偏离（不填或 0.01 到 0.99），四种方案随机带有效期（不填或任意带时区的时刻），让单调性性质测试覆盖新规则

  验证：运行 `uv run pytest tests/modules/risk`，确认因为配置、函数和字段不存在而失败
- [x] 2.2 实现（design D2、D3）：
  - `config.py`：`price_deviation_min`、`price_deviation_max`、`price_deviation_default`
  - `contracts/risk.py`：`RiskDecision` 增加 `expires_at: AwareDatetime | None`，更新说明
  - `domain/exposure.py`：`execution_session`、`effective_expiry`、`effective_price_deviation`；`execution_day` 改为调用 `execution_session`
  - `domain/rules.py`：`price_deviation_range`，登记到 `OPENING_RULES`
  - `domain/engine.py`：通过和调整时填 `expires_at`，拒绝时为 `None`

  验证：
  - 2.1 的测试和全部已有测试通过，包括单调性性质测试
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
  - `uv run pytest tests/architecture` 通过

## 3. 再校验的纯逻辑

- [x] 3.1 先写测试：
  - `tests/modules/risk/domain/test_reservation.py`（design D4）：
    - 没有再校验过时，预留股数是首次检查的最终股数
    - `recheck(decision)` 后预留股数是再校验的最终股数，`decision` 不变，剩余股数随之变化
    - 已结束的预留 `recheck` 原样返回
  - 新建 `tests/modules/risk/domain/test_recheck.py`，直接构造 `RiskRequest`（预留里已去掉方案自己的）和 `Reservation`（design D5）：
    - 有效期：当前时刻早于、等于、晚于生效的有效期；退出方案同样作废
    - 价格偏离：等于允许值（51.5、48.5）不作废；超过（51.55、48.45）作废；方案写了 0.05 时 52.5 和 52.55；退出方案的限价 120、实时价 100 不作废
    - 跌破止损价：止损价 49 时实时价 49 作废、49.01 不作废；实时价 48 时原因同时有"价格偏离"和"跌破止损价"
    - 作废时不重跑规则：同时熔断已经触发，原因里只有作废的几项
    - 请求股数：买入用预留股数，不放大；清仓用实时可卖数量；减仓超过实时持仓时调整
    - 结果的生效有效期等于首次检查的；`plan_ended()` 是拒绝，原因是"方案已结束"

  验证：运行，确认因为 `domain/recheck.py` 和 `Reservation.recheck` 不存在而失败
- [x] 3.2 实现（design D4、D5）：
  - `domain/reservation.py`：`rechecked` 字段，`quantity` 优先取它，`recheck(decision)` 方法
  - 新建 `domain/recheck.py`：`OutsideSessionError`、`recheck_plan`、`plan_ended`
  - `modules/risk/__init__.py` 增加导出 `OutsideSessionError`

  验证：
  - 3.1 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
  - `uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库，文件之间没有循环 import）

## 4. 再校验的用例

- [x] 4.1 先写测试：
  - `tests/modules/risk/test_service.py`，用 `FakeClock` 拨动时间：
    - 和 `_check` 一样，增加本文件内的再校验辅助函数，默认用基准账户、方案的限价作为实时价、熔断没有触发
    - pre-order-recheck spec 的全部场景：「开盘前调用时报错」、有效期四个、价格偏离五个、跌破止损价三个、重跑风控六个、再校验后的预留两个、已经结束的方案三个
    - risk-reservations spec「再校验和检查同时进行」：两种先后顺序各跑一遍（design D7）

  验证：运行，确认因为 `RiskService.recheck` 不存在而失败
- [x] 4.2 实现 `RiskService.recheck`（design D6）：
  - 不在盘中时抛 `OutsideSessionError`，不读写存储
  - 没有有效预留时返回 `plan_ended()`
  - 否则用去掉自己的有效预留调用 `recheck_plan`，按结果 `recheck` 或 `release` 预留
  - `check` 和 `recheck` 组装请求的部分抽成私有方法；更新类的说明

  验证：
  - 4.1 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
- [x] 4.3 扩展性质测试 `test_holdings_plus_reservations_never_exceed_the_limits`（design D8）：步骤里加入"再校验已检查过的第几个方案"，用同一份账户，实时价等于限价（市价单的退出方案用任意正数）。每一步之后断言：
  - 持仓加全部有效预留仍然不超限
  - 买入、加仓、减仓的再校验最终股数不超过再校验前的预留股数

  验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/risk` 通过
  - 统计生成的样例：有相当比例的再校验步骤作用在有效预留上，并且结果是通过或调整
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 5. 整体检查

- [x] 5.1 按 CI 的顺序在本地完整跑一遍：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1 validate --all --strict --no-interactive`。验证：
  - 全部通过
  - 逐条核对三份 spec 的每个场景都有对应的测试
  - `git status` 里没有探针文件和缓存目录残留
