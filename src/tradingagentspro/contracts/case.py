"""案件类型：开始运行时由调度器确定，固定问题、结论种类和优先级，见 case-types spec「案件类型」。

案件记录、按类型评估和任务派发都要用，所以放在 contracts。
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
