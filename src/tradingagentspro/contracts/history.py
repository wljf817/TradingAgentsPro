"""一只股票的历史：案件上下文里的索引和 query_history 的结果，见 history-query spec。"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.conclusion import Conclusion
from tradingagentspro.contracts.review import CriticReview
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade import OrderStatus, TradeKind
from tradingagentspro.contracts.trade_plan import TradePlan


class HistoryIndex(BaseModel):
    """这只股票的历史案件数和交易数。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cases: int
    trades: int


class HistoryCase(BaseModel):
    """一个已结束的案件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: UUID
    case_type: CaseType
    reason: str  # 触发原因
    ended_at: AwareDatetime
    end_reason: CaseEndReason
    conclusion: Conclusion | None
    plan: TradePlan | None
    reviews: tuple[CriticReview, ...]
    risk: RiskDecision | None


class HistoryTrade(BaseModel):
    """一笔有成交的交易。realized_pnl 是这笔的已实现盈亏，买入没有。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_order_id: str
    case_id: UUID | None
    kind: TradeKind
    quantity: int
    filled: int
    average_price: Decimal
    fees: Decimal
    status: OrderStatus
    traded_on: date
    realized_pnl: Decimal | None


class HistoryReport(BaseModel):
    """一次查询的结果：总数是符合条件的全部，案件和交易不超过条数上限。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    total_cases: int
    cases: tuple[HistoryCase, ...]
    total_trades: int
    trades: tuple[HistoryTrade, ...]
    realized_pnl: Decimal  # 符合条件的全部卖出和止损单的合计
