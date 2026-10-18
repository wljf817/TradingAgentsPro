"""PM 的结论：持仓、关注、候选、寻股四种，每种只有这个状态允许的决定。

结论来自模型输出，所以在这里校验格式，见 case-types spec「结论的公共部分」到「跟进申请的格式」。
哪个决定附什么由各结论的 validator 按 spec 的表检查；和案件类型有关的检查（结论种类、跟进、续期、
研究要求）由调度器的结论校验负责。

持仓、关注、候选的结论各带一个信号，寻股每只加入的候选一个，格式见 signal-format spec，不能是
无信号；信号的评估窗口和数据截至时刻由 Agent 网关检查（spec「结论里的信号」）。
"""

from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from tradingagentspro.contracts.signal import Signal
from tradingagentspro.contracts.thesis import (
    CatalystReference,
    Condition,
    InvestmentThesis,
    WatchThesis,
)
from tradingagentspro.contracts.trade_plan import (
    OrderType,
    TradeAction,
    TradePlan,
    check_plan_fields,
    check_price_tick,
)


class Decision(StrEnum):
    """结论的主决定。每种结论只能选其中属于自己状态的几个。"""

    HOLD = "hold"  # 持有
    ADD = "add"  # 加仓
    REDUCE = "reduce"  # 减仓
    CLOSE = "close"  # 清仓
    BUY = "buy"  # 开仓方案
    KEEP_WATCHING = "keep_watching"  # 继续关注
    DEMOTE = "demote"  # 降为候选
    REMOVE = "remove"  # 移出股票池
    PROMOTE = "promote"  # 升级为关注
    KEEP = "keep"  # 保持


class AfterClose(StrEnum):
    """PM 发起的清仓之后的去向。"""

    WATCH = "watch"  # 转为关注，附关注论点
    REMOVE = "remove"  # 移出股票池


# 写交易方案的决定和方案的动作
_ACTIONS: Final = {
    Decision.ADD: TradeAction.ADD,
    Decision.REDUCE: TradeAction.REDUCE,
    Decision.CLOSE: TradeAction.CLOSE,
    Decision.BUY: TradeAction.BUY,
}


class PlanDraft(BaseModel):
    """PM 写的交易方案。动作由结论的决定给出；方案 ID、股票和板块由代码补上。

    各字段的含义和 TradePlan 相同；按动作检查字段由结论的 validator 调用 check_plan_fields。
    有效期和允许价格偏离可以不写，由风控使用默认值。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity: int | None = Field(default=None, gt=0)
    order_type: OrderType
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    emergency_threshold: Decimal | None = Field(default=None, gt=0, lt=1)
    expires_at: AwareDatetime | None = None
    max_price_deviation: Decimal | None = Field(default=None, gt=0, lt=1)

    @field_validator("limit_price", "stop_price")
    @classmethod
    def _check_tick(cls, price: Decimal | None, info: ValidationInfo) -> Decimal | None:
        check_price_tick(info.field_name or "", price)
        return price


class FollowUpReason(StrEnum):
    """跟进申请的原因类型。"""

    EVENT_REACTION = "event_reaction"  # 事件后反应
    EXTERNAL_SCHEDULE = "external_schedule"  # 外部日程
    OBSERVATION = "observation"  # 价格或指标观察


class FollowUpRequest(BaseModel):
    """跟进申请。引用和原因是否对应、触发时间的范围等审批规则在「跟进审批」。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    reason: FollowUpReason
    # 引用的事件或日程项，或者代码能检测的条件。两种引用的字段不重叠，联合类型分得清。
    # 可以不写：没有引用具体对象的申请由「跟进审批」拒绝，并提示改为调整复查日
    reference: CatalystReference | Condition | None = None
    question: str = Field(min_length=1)
    possible_actions: str = Field(min_length=1)  # 可能采取的动作
    trigger_at: AwareDatetime  # 触发时间


def _check_plan(decision: Decision, plan: PlanDraft | None) -> None:
    """加仓、减仓、清仓和开仓方案必须写方案并按动作检查，买入和加仓还要写紧急阈值；其他决定不写。"""
    action = _ACTIONS.get(decision)
    if action is None:
        if plan is not None:
            raise ValueError("只有加仓、减仓、清仓和开仓方案写交易方案")
        return
    if plan is None:
        raise ValueError("加仓、减仓、清仓和开仓方案必须写交易方案")
    check_plan_fields(action, plan)
    if action.is_opening and plan.emergency_threshold is None:
        raise ValueError("买入和加仓必须写紧急阈值")


def _trade_plan(
    decision: Decision, plan: PlanDraft | None, plan_id: UUID, symbol: str, sector: str
) -> TradePlan | None:
    if plan is None:
        return None
    return TradePlan(
        plan_id=plan_id,
        symbol=symbol,
        sector=sector,
        action=_ACTIONS[decision],
        **plan.model_dump(),
    )


class _ConclusionBase(BaseModel):
    """四种结论的公共部分。方案的理由和引用、PM 发起的移出的理由，就是这里的理由和引用。

    信号不在这里：寻股的结论没有自己的信号。信号自带理由和引用，不借用这里的。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    answer: str = Field(min_length=1)  # 对案件问题的正面回答
    rationale: str = Field(min_length=1)  # 理由；无动作时也要写
    cited_results: tuple[str, ...] = ()  # 引用的 Result ID


class HoldingConclusion(_ConclusionBase):
    """持仓的结论，见 spec「持仓的结论」。

    论点更新 thesis 是一份完整的投资论点，整份代替原来的；清仓后转为关注时附 watch_thesis。
    """

    signal: Signal  # PM 对这只股票的信号；信号里的评分记为股票池的最新评分
    decision: Literal[Decision.HOLD, Decision.ADD, Decision.REDUCE, Decision.CLOSE]
    plan: PlanDraft | None = None
    thesis: InvestmentThesis | None = None  # 论点更新
    follow_up: FollowUpRequest | None = None
    after_close: AfterClose | None = None
    watch_thesis: WatchThesis | None = None  # 清仓后转为关注时的关注论点

    @model_validator(mode="after")
    def _check_attachments(self) -> Self:
        _check_plan(self.decision, self.plan)
        if self.decision is Decision.CLOSE:
            if self.after_close is None:
                raise ValueError("清仓必须写清仓后的去向")
            if (self.after_close is AfterClose.WATCH) != (self.watch_thesis is not None):
                raise ValueError("清仓后转为关注必须附关注论点，移出不附")
            if self.thesis is not None or self.follow_up is not None:
                raise ValueError("清仓不能附论点更新和跟进申请")
        elif self.after_close is not None or self.watch_thesis is not None:
            raise ValueError("只有清仓写去向和关注论点")
        return self

    def trade_plan(self, plan_id: UUID, symbol: str, sector: str) -> TradePlan | None:
        """补上方案 ID、股票和板块，变成交易方案；没有方案时为 None。"""
        return _trade_plan(self.decision, self.plan, plan_id, symbol, sector)


class WatchConclusion(_ConclusionBase):
    """关注的结论，见 spec「关注的结论」。

    thesis 是开仓方案附的新建仓的投资论点；论点更新 watch_thesis 是一份完整的关注论点。
    """

    signal: Signal  # PM 对这只股票的信号；信号里的评分记为股票池的最新评分
    decision: Literal[Decision.BUY, Decision.KEEP_WATCHING, Decision.DEMOTE, Decision.REMOVE]
    plan: PlanDraft | None = None
    thesis: InvestmentThesis | None = None  # 新建仓的投资论点
    watch_thesis: WatchThesis | None = None  # 论点更新
    follow_up: FollowUpRequest | None = None

    @model_validator(mode="after")
    def _check_attachments(self) -> Self:
        _check_plan(self.decision, self.plan)
        if (self.decision is Decision.BUY) != (self.thesis is not None):
            raise ValueError("开仓方案必须附投资论点，其他决定不附")
        if self.decision is not Decision.KEEP_WATCHING and (
            self.watch_thesis is not None or self.follow_up is not None
        ):
            raise ValueError("只有继续关注可以附论点更新和跟进申请")
        return self

    def trade_plan(self, plan_id: UUID, symbol: str, sector: str) -> TradePlan | None:
        """补上方案 ID、股票和板块，变成交易方案；没有方案时为 None。"""
        return _trade_plan(self.decision, self.plan, plan_id, symbol, sector)


class CandidateConclusion(_ConclusionBase):
    """候选的结论，见 spec「候选的结论」。"""

    signal: Signal  # PM 对这只股票的信号；信号里的评分记为股票池的最新评分
    decision: Literal[Decision.PROMOTE, Decision.KEEP, Decision.REMOVE]
    watch_thesis: WatchThesis | None = None  # 升级时的关注论点

    @model_validator(mode="after")
    def _check_attachments(self) -> Self:
        if (self.decision is Decision.PROMOTE) != (self.watch_thesis is not None):
            raise ValueError("升级必须附关注论点，保持和移出不附")
        return self


class PoolAddition(BaseModel):
    """寻股加入的一只候选。加入是否通过由股票池检查，来源是 PM 探索。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=1)
    reason: str = Field(min_length=1)  # 理由与论点草稿
    signal: Signal  # PM 对这只候选的信号；加入以后信号里的评分记为股票池的最新评分


class PoolRemoval(BaseModel):
    """寻股删除的一只股票。删除是否通过由股票池检查。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ExplorationConclusion(_ConclusionBase):
    """寻股的结论：只增删股票池，两个列表都可以为空。没有自己的信号，每只加入的候选各有一个。"""

    additions: tuple[PoolAddition, ...] = ()
    removals: tuple[PoolRemoval, ...] = ()


# 候选的结论排在关注的结论前面：候选决定移出的结论的字段是关注的结论的子集，按 JSON 读回时两种都
# 能通过，pydantic 取左边的。其余的决定和字段各不相同，只有一种能通过
type Conclusion = HoldingConclusion | CandidateConclusion | WatchConclusion | ExplorationConclusion
