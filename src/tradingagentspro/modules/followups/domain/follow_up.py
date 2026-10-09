"""跟进的记录：一个获批的跟进申请一条，以及按 follow-ups spec「跟进的状态」表改变状态的方法。

替换时新建一条记录，旧的留着（被替换）；每天的申请次数从记录的获批时刻数出来。
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from tradingagentspro.contracts.conclusion import FollowUpRequest


class FollowUpStatus(StrEnum):
    """跟进的状态。只有待执行的会变化，其余三种不再变化。"""

    PENDING = "pending"  # 待执行
    TRIGGERED = "triggered"  # 已触发
    REPLACED = "replaced"  # 被替换：这只股票原因相同的新申请获批
    VOIDED = "voided"  # 已作废：股票的状态往下走


@dataclass(frozen=True, slots=True)
class FollowUp:
    """一个获批的跟进。request 是申请的原样内容；获批的申请都引用了具体对象。

    状态变化的方法返回新的记录；不是待执行的跟进再变化时抛 ValueError：service 只对待执行的
    跟进调用，抛出说明调用有错。
    """

    follow_up_id: UUID
    symbol: str
    request: FollowUpRequest
    case_id: UUID  # 来源案件：提出申请的案件
    requested_at: datetime  # 获批时刻
    status: FollowUpStatus = FollowUpStatus.PENDING

    def triggered(self) -> Self:
        """到了触发时间。"""
        return self._settled(FollowUpStatus.TRIGGERED)

    def replaced(self) -> Self:
        """这只股票原因相同的新申请获批。"""
        return self._settled(FollowUpStatus.REPLACED)

    def voided(self) -> Self:
        """这只股票的状态往下走。"""
        return self._settled(FollowUpStatus.VOIDED)

    def _settled(self, status: FollowUpStatus) -> Self:
        if self.status is not FollowUpStatus.PENDING:
            raise ValueError(f"跟进 {self.follow_up_id} 已经是 {self.status}，不能再变化")
        return replace(self, status=status)
