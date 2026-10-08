"""案件类型和结束原因。

案件类型在开始运行时由调度器确定，固定问题、结论种类和优先级，见 case-types spec「案件类型」；
案件记录、按类型评估和任务派发都要用。结束原因由调度器的案件状态给出，见 case-stages spec
「结束原因」；案件记录、收盘检查和复盘的反事实评估都要读。所以都放在 contracts。
"""

from enum import StrEnum


class CaseType(StrEnum):
    """案件类型。复查（review）是 PM 回答问题的例行检查，复盘（retrospective）由 Reviewer 处理。"""

    HOLDING_RISK_EVENT = "holding_risk_event"  # 持仓风险事件
    HOLDING_EVENT = "holding_event"  # 持仓事件
    HOLDING_REVIEW = "holding_review"  # 持仓复查
    HOLDING_CATALYST_REVIEW = "holding_catalyst_review"  # 持仓催化剂复查
    HOLDING_FOLLOW_UP = "holding_follow_up"  # 持仓跟进
    WATCH_EVENT = "watch_event"  # 关注股事件
    WATCH_REVIEW = "watch_review"  # 关注复查
    WATCH_CATALYST_REVIEW = "watch_catalyst_review"  # 关注催化剂复查
    WATCH_FOLLOW_UP = "watch_follow_up"  # 关注跟进
    CANDIDATE_EVENT = "candidate_event"  # 候选股事件
    CANDIDATE_EVALUATION = "candidate_evaluation"  # 候选评估
    EXPLORATION = "exploration"  # 寻股
    WEEKLY_RETROSPECTIVE = "weekly_retrospective"  # 每周复盘
    SELL_RETROSPECTIVE = "sell_retrospective"  # 卖出复盘


class CaseEndReason(StrEnum):
    """案件的结束原因，见 case-stages spec「结束原因」。"""

    NO_PLAN = "no_plan"  # 结论没有交易方案
    ORDER_SUBMITTED = "order_submitted"  # 订单已提交
    VETOED = "vetoed"  # Critic 否决开仓类方案
    WITHDRAWN = "withdrawn"  # PM 撤回方案
    RISK_REJECTED = "risk_rejected"  # 风控拒绝
    HUMAN_REJECTED = "human_rejected"  # 人工拒绝
    CONFIRMATION_TIMED_OUT = "confirmation_timed_out"  # 确认超时
    RECHECK_VOIDED = "recheck_voided"  # 下单前再校验作废
    PLAN_VOIDED = "plan_voided"  # 方案被作废：这只股票有新案件排队
    EMERGENCY_ABORTED = "emergency_aborted"  # 紧急退出中止
    INVALID_CONCLUSION = "invalid_conclusion"  # 结论重交用完仍不通过
    FAILED = "failed"  # 系统故障
    RETROSPECTIVE_DONE = "retrospective_done"  # 复盘完成
