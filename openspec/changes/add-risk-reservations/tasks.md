# Tasks

## 1. 方案 ID

- [x] 1.1 先写测试：
  - `tests/contracts/test_trade_plan.py`：
    - 增加 risk-rules spec「没有方案 ID」：不填方案 ID 的买入方案和清仓方案、方案 ID 为 `"plan-1"`，都断言抛出 `ValidationError`
    - 已有场景构造的方案补上方案 ID
  - `tests/modules/risk/builders.py`：增加 `next_plan_id()`，用计数器依次给出 `UUID(int=1)`、`UUID(int=2)`……（design D6）。`baseline_plan`、`add_aaa`、`reduce_plan`、`close_plan` 默认用它，也可以由参数指定
  - `tests/modules/risk/domain/strategies.py`：生成的方案用 `st.uuids()` 作为方案 ID

  验证：运行 `uv run pytest`，确认因为 `TradePlan` 不接受 `plan_id` 而失败
- [x] 1.2 `contracts/trade_plan.py`：`TradePlan` 增加必填字段 `plan_id: UUID`，更新类的说明（design D6）。验证：
  - 全部测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 2. 预留记录和额度计算

- [x] 2.1 先写测试 `tests/modules/risk/domain/test_reservation.py`，直接构造记录和账户：
  - spec「新建仓的预留」「加仓的预留按当前持仓计算增加的风险」
  - 卖出预留的股数等于剩余股数：减仓 AAA 60 股，累计成交 30 股后是 30 股
  - 记录的状态变化（design D1）：
    - 部分成交后的剩余股数
    - 同一个累计成交股数重复上报，结果不变
    - 累计成交达到预留股数后结束
    - 释放后结束
    - 已结束的记录再 `fill` 或 `release` 都原样返回

  验证：运行，确认因为 `domain/reservation.py` 不存在而失败
- [x] 2.2 实现 `modules/risk/domain/reservation.py`（design D1、D2）：
  - `Reservation`：剩余股数、是否有效、`fill`、`release`
  - `opening_reservation`、`sell_reservation`，计算调用 `exposure.py` 已有的函数
  - `modules/risk/__init__.py` 增加导出 `Reservation`

  验证：
  - 2.1 的测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
  - `uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 3. 存储和用例

- [x] 3.1 先写测试：
  - `tests/fakes/reservations.py`：`InMemoryReservationStore`，按方案 ID 存在 dict 里（design D8）
  - `tests/modules/risk/builders.py`：增加 `risk_service(store, **changes)`，用 `FakeClock(BASELINE_NOW)`、纽交所样例日历和默认阈值创建 `RiskService`
  - `tests/modules/risk/test_service.py`：
    - spec「通过后预留，后续方案按剩余额度检查」「退出方案预留卖出股数」
    - spec「重复检查返回第一次的结果」「被拒绝的方案再次检查时重新判定」
    - spec「重启后预留仍然计入」的第一步：用同一个存储新建 `RiskService` 后，预留仍然计入
    - spec「同时提交的两个方案」：两种先后顺序各检查一遍，都满足 spec 的结果（design D5）

  验证：运行，确认因为 `ports.py` 和 `service.py` 不存在而失败
- [x] 3.2 实现（design D3、D4、D8）：
  - `modules/risk/ports.py`：`ReservationStore`
  - `modules/risk/service.py`：`RiskService.__init__` 和 `check`
  - `modules/risk/__init__.py` 增加导出 `RiskService`、`ReservationStore`

  验证：
  - 3.1 的测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过，module-layers 契约包括新的 service 和 ports
- [x] 3.3 先写测试，写在 `test_service.py`：
  - spec「卖出预留随成交缩小」「部分成交后剩余部分仍然占用额度」「全部成交后预留结束」
  - spec「方案在下单前结束时全部释放」「订单结束时释放没成交的部分」「一股都没成交就过期的订单全部释放」
  - spec「已结束的预留不再变化」「没有预留的方案」
  - spec「重启后预留仍然计入」的第二步：重启后释放，再检查

  验证：运行，确认因为 `record_fill` 和 `release` 还没有实现而失败
- [x] 3.4 实现 `RiskService.record_fill` 和 `RiskService.release`：没有记录时什么都不做（design D3）。验证：
  - 3.3 和之前的测试都通过
  - `uv run mypy`、`uv run ruff check` 通过

## 4. 合计不超限的性质测试

- [x] 4.1 写 Hypothesis 性质测试（design D9）：
  - `strategies.py`：
    - 公开方案的策略
    - 增加一开始就满足各项上限的账户：先生成持仓，再按持仓算出满足组合合计风险和板块集中度所需的最低权益，权益在它之上生成；没有最近成交日
    - 增加多半能通过的开仓方案：止损价是限价的 50% 到 99%，不填紧急阈值。只用前者生成的方案大多被拒绝，几乎不会同时有两份开仓预留
  - `test_service.py` 的性质测试：
    - 依次检查任意一串方案（任意方案和多半能通过的开仓方案混合），中间随机释放一些预留
    - 每一步之后，持仓加全部有效预留仍然满足：已结算资金、组合合计风险、板块集中度、最多持仓数；每只股票的卖出预留之和不超过持仓股数

  验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/risk` 通过
  - 统计生成的样例：开仓方案有相当比例通过或调整，并且有样例同时持有两份以上开仓预留
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 5. 整体检查

- [x] 5.1 按 CI 的顺序在本地完整跑一遍：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1 validate --all --strict --no-interactive`。验证：
  - 全部通过
  - 逐条核对两份 spec 的每个场景都有对应的测试
  - `git status` 里没有探针文件和缓存目录残留
