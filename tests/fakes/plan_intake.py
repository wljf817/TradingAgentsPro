"""测试用的提交接口：按给定的持仓回答板块，记下提交的方案，测试用它安排风控结果、出错和卡住。"""

import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan

# 默认风控结果的生效有效期；回复里不写它，取什么值都行
_EXPIRES_AT = datetime(2026, 10, 14, 20, tzinfo=UTC)
WAIT_SECONDS = 5  # 卡住的提交最多等这么久，免得测试出错时一直挂着


def passed(plan: TradePlan) -> RiskDecision:
    """通过：最终股数是方案的股数，清仓是 100；开仓方案的紧急阈值 0.08。"""
    return RiskDecision(
        outcome=RiskOutcome.PASSED,
        quantity=100 if plan.quantity is None else plan.quantity,
        emergency_threshold=Decimal("0.08") if plan.action.is_opening else None,
        expires_at=_EXPIRES_AT,
        violations=(),
    )


class FakePlanIntake:
    """满足 PlanIntake。

    - sectors：有持仓的股票和它的板块，held_sector 按它回答
    - looked_up、submitted：按顺序记下查过的股票和提交的方案
    - threads：查持仓和提交运行的线程
    - log：每次查持仓、提交时追加一条，可以和 FakeCall 共用，用来检查先后
    - decide：给出风控结果，默认 passed
    - lookup_error、submit_error：设上以后，查持仓、提交时抛它
    - hold：设上以后，提交先等它被 set 再返回
    - returned：每次提交返回或抛错以后 set
    """

    def __init__(
        self, sectors: Mapping[str, str] | None = None, log: list[str] | None = None
    ) -> None:
        self.sectors = dict(sectors or {})
        self.log = [] if log is None else log
        self.looked_up: list[str] = []
        self.submitted: list[TradePlan] = []
        self.threads: list[threading.Thread] = []
        self.decide: Callable[[TradePlan], RiskDecision] = passed
        self.lookup_error: Exception | None = None
        self.submit_error: Exception | None = None
        self.hold: threading.Event | None = None
        self.returned = threading.Event()

    def held_sector(self, symbol: str) -> str | None:
        self.looked_up.append(symbol)
        self.threads.append(threading.current_thread())
        self.log.append(f"held_sector:{symbol}")
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.sectors.get(symbol)

    def submit(self, plan: TradePlan) -> RiskDecision:
        self.submitted.append(plan)
        self.threads.append(threading.current_thread())
        self.log.append(f"submit:{plan.plan_id}")
        try:
            if self.hold is not None:
                self.hold.wait(WAIT_SECONDS)
            if self.submit_error is not None:
                raise self.submit_error
            return self.decide(plan)
        finally:
            self.returned.set()
