"""跟进审批（自定义跟进）：审批 PM 在持仓和关注的结论里附的跟进申请，不合规则的拒绝，通过的保存为
待执行的跟进，同一只股票原因相同的替换旧的；到点给出跟进，由调用方转成跟进的触发入队；股票状态
往下走（清仓、关注降为候选、移出股票池）时作废这只股票待执行的跟进。

审批由代码执行，不调用模型。跟进存在状态库的 followups_follow_ups 表里（repo.py），每个动作写一条
程序日志，行为者是调度器；用例的四个公开方法一次只进行一个。规则见 openspec 的 follow-ups spec。
模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.followups.config import FollowUpConfig
from tradingagentspro.modules.followups.domain.follow_up import FollowUp, FollowUpStatus
from tradingagentspro.modules.followups.domain.rules import (
    FollowUpRejection,
    due,
    request_rejection,
    same_reason,
    trigger_limit,
    voids_follow_ups,
)
from tradingagentspro.modules.followups.ports import FollowUpStore
from tradingagentspro.modules.followups.repo import PostgresFollowUpStore
from tradingagentspro.modules.followups.service import FollowUpService

__all__ = [
    "FollowUp",
    "FollowUpConfig",
    "FollowUpRejection",
    "FollowUpService",
    "FollowUpStatus",
    "FollowUpStore",
    "PostgresFollowUpStore",
    "due",
    "request_rejection",
    "same_reason",
    "trigger_limit",
    "voids_follow_ups",
]
