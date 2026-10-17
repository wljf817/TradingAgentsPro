"""跟进模块需要的外部接口。"""

from datetime import datetime
from typing import Protocol

from tradingagentspro.modules.followups.domain.follow_up import FollowUp


class FollowUpStore(Protocol):
    """跟进的存储：按跟进 ID 保存每个跟进最新的记录。

    数据库实现（repo.py）只追加不覆盖：状态有变化时写入一个新版本，读取时取每个跟进的最新版本，
    见 state-store spec。
    """

    def pending(self) -> tuple[FollowUp, ...]:
        """全部待执行的跟进。"""
        ...

    def requested_since(self, moment: datetime) -> tuple[FollowUp, ...]:
        """获批时刻不早于 moment 的跟进，不论现在的状态；用来数当天的申请次数。"""
        ...

    def save(self, follow_up: FollowUp) -> None:
        """保存这个跟进最新的记录：新的跟进新增，状态变了的取代原来的版本。"""
        ...
