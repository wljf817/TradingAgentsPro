"""风控引擎：按开仓类和减仓类规则检查交易方案，给出通过、调整或拒绝；通过的方案预留额度；
提交订单前用实时数据再校验。

规则见 openspec 的 risk-rules spec，预留见 risk-reservations spec，再校验见 pre-order-recheck spec。
模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.risk.config import RiskConfig
from tradingagentspro.modules.risk.domain.engine import evaluate_plan
from tradingagentspro.modules.risk.domain.recheck import OutsideSessionError
from tradingagentspro.modules.risk.domain.request import (
    OpeningReservation,
    RiskRequest,
    SellReservation,
)
from tradingagentspro.modules.risk.domain.reservation import Reservation
from tradingagentspro.modules.risk.ports import ReservationStore
from tradingagentspro.modules.risk.service import RiskService

__all__ = [
    "OpeningReservation",
    "OutsideSessionError",
    "Reservation",
    "ReservationStore",
    "RiskConfig",
    "RiskRequest",
    "RiskService",
    "SellReservation",
    "evaluate_plan",
]
