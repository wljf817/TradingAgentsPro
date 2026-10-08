"""下单流程的入口：/plan 的提交接口和确认按钮的回调。

「下单流程」接上之前：查持仓的板块已经用执行模块的账户数据；提交和按钮回调一律报错，
见 bootstrap spec「下单流程接上之前」和 add-bootstrap-wiring 的 design D9。
「下单流程」替换 submit 和 refuse_decision。
"""

from collections.abc import Callable
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import Account
from tradingagentspro.modules.notification import ConfirmationOutcome


class OrderFlowMissingError(RuntimeError):
    """下单流程还没接上：方案没有提交，按钮的结果没有送达。"""


class AccountPlanIntake:
    """满足 PlanIntake。持仓的板块从账户数据查；提交在下单流程接上之前一律报错。

    account 是执行模块的账户数据，装配时传 ExecutionService.account；
    读券商账户失败时它抛的错原样抛出。
    """

    def __init__(self, account: Callable[[], Account]) -> None:
        self._account = account

    def held_sector(self, symbol: str) -> str | None:
        positions = self._account().positions
        return next((p.sector for p in positions if p.symbol == symbol), None)

    def submit(self, plan: TradePlan) -> RiskDecision:
        raise OrderFlowMissingError("下单流程还没接上，方案没有提交")


def refuse_decision(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
    """满足 DecisionHandler：下单流程接上之前没有方案在等确认，一律报错。"""
    raise OrderFlowMissingError("下单流程还没接上，没有方案在等确认")
