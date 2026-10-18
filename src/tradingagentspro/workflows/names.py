"""几个流程的日志、告警和退回原因共用的中文名：方案动作、订单类型、订单状态、买卖方向、止损单状态，
以及案件类型、阶段、结束原因、Critic 审核的结果、事件类别、决定、审核、回应和 agent。
"""

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.conclusion import Decision
from tradingagentspro.contracts.event import EventCategory
from tradingagentspro.contracts.review import ResponseDecision, Verdict
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction
from tradingagentspro.modules.execution import OrderSide, OrderStatus, StopStatus
from tradingagentspro.modules.scheduler import CaseStage, CriticOutcome

ACTIONS = {
    TradeAction.BUY: "买入",
    TradeAction.ADD: "加仓",
    TradeAction.REDUCE: "减仓",
    TradeAction.CLOSE: "清仓",
}

ORDER_TYPES = {OrderType.LIMIT: "限价单", OrderType.MARKET: "市价单"}
SIDES = {OrderSide.BUY: "买入", OrderSide.SELL: "卖出"}
ORDER_STATUSES = {
    OrderStatus.PENDING: "待提交",
    OrderStatus.SUBMITTED: "已提交",
    OrderStatus.PARTIALLY_FILLED: "部分成交",
    OrderStatus.FILLED: "全部成交",
    OrderStatus.CANCELLED: "已撤销",
    OrderStatus.EXPIRED: "已过期",
    OrderStatus.REJECTED: "已拒绝",
    OrderStatus.UNKNOWN: "状态未知",
}
STOP_STATUSES = {
    StopStatus.PENDING: "还没挂出去",
    StopStatus.WORKING: "挂着",
    StopStatus.UNKNOWN: "状态未知",
    StopStatus.ENDED: "已经结束",
}

CASE_TYPES = {
    CaseType.HOLDING_RISK_EVENT: "持仓风险事件",
    CaseType.HOLDING_EVENT: "持仓事件",
    CaseType.HOLDING_REVIEW: "持仓复查",
    CaseType.HOLDING_CATALYST_REVIEW: "持仓催化剂复查",
    CaseType.HOLDING_FOLLOW_UP: "持仓跟进",
    CaseType.WATCH_EVENT: "关注股事件",
    CaseType.WATCH_REVIEW: "关注复查",
    CaseType.WATCH_CATALYST_REVIEW: "关注催化剂复查",
    CaseType.WATCH_FOLLOW_UP: "关注跟进",
    CaseType.CANDIDATE_EVENT: "候选股事件",
    CaseType.CANDIDATE_EVALUATION: "候选评估",
    CaseType.EXPLORATION: "寻股",
    CaseType.WEEKLY_RETROSPECTIVE: "每周复盘",
    CaseType.SELL_RETROSPECTIVE: "卖出复盘",
}
CASE_STAGES = {
    CaseStage.CONCLUDING: "唤醒 PM",
    CaseStage.CRITIC_REVIEW: "Critic 审核",
    CaseStage.PM_RESPONSE: "PM 回应",
    CaseStage.RISK_CHECK: "风控",
    CaseStage.AWAITING_CONFIRMATION: "等人工确认",
    CaseStage.AWAITING_ORDER: "等下单",
    CaseStage.RETROSPECTIVE: "复盘",
    CaseStage.ENDED: "已结束",
}
END_REASONS = {
    CaseEndReason.NO_PLAN: "没有交易方案",
    CaseEndReason.ORDER_SUBMITTED: "订单已提交",
    CaseEndReason.VETOED: "Critic 否决",
    CaseEndReason.WITHDRAWN: "PM 撤回",
    CaseEndReason.RISK_REJECTED: "风控拒绝",
    CaseEndReason.HUMAN_REJECTED: "人工拒绝",
    CaseEndReason.CONFIRMATION_TIMED_OUT: "确认超时",
    CaseEndReason.RECHECK_VOIDED: "再校验作废",
    CaseEndReason.PLAN_VOIDED: "方案被作废",
    CaseEndReason.EMERGENCY_ABORTED: "紧急退出中止",
    CaseEndReason.INVALID_CONCLUSION: "结论无效",
    CaseEndReason.FAILED: "系统故障",
    CaseEndReason.RETROSPECTIVE_DONE: "复盘完成",
}
CRITIC_OUTCOMES = {
    CriticOutcome.APPROVED: "Critic 通过",
    CriticOutcome.DISPUTED: "有分歧",
    CriticOutcome.UNAVAILABLE: "Critic 不可用",
}
EVENT_CATEGORIES = {
    EventCategory.PRICE_VOLUME: "价格和成交量异动",
    EventCategory.NEWS: "新闻",
    EventCategory.FILING: "公告",
    EventCategory.EARNINGS: "财报发布",
    EventCategory.ENTRY: "入场条件满足",
    EventCategory.THESIS_CONDITION: "达到目标价或证伪条件触发",
}
DECISIONS = {
    Decision.HOLD: "持有",
    Decision.ADD: "加仓",
    Decision.REDUCE: "减仓",
    Decision.CLOSE: "清仓",
    Decision.BUY: "开仓方案",
    Decision.KEEP_WATCHING: "继续关注",
    Decision.DEMOTE: "降为候选",
    Decision.REMOVE: "移出",
    Decision.PROMOTE: "升级",
    Decision.KEEP: "保持",
}
VERDICTS = {Verdict.APPROVE: "通过", Verdict.CHALLENGE: "质询", Verdict.VETO: "否决"}
RESPONSES = {
    ResponseDecision.MAINTAIN: "维持",
    ResponseDecision.MODIFY: "修改",
    ResponseDecision.WITHDRAW: "撤回",
}
AGENTS = {
    Agent.PM: "PM",
    Agent.NEWS: "News",
    Agent.QUANT: "Quant",
    Agent.FUNDAMENTALS: "Fundamentals",
    Agent.CRITIC: "Critic",
    Agent.REVIEWER: "Reviewer",
}
