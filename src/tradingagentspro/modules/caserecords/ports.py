"""案件记录模块需要的外部接口。"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.modules.caserecords.domain.record import (
    CaseRecord,
    Finding,
    ProposalRecord,
)


class CaseRecordStore(Protocol):
    """案件记录的存储：案件记录和方案记录按案件 ID 只追加版本，读取时取最新版本；发现一项一行，
    只追加。

    数据库实现是 repo 的 PostgresCaseRecordStore（表 caserecords_cases、caserecords_proposals、
    caserecords_findings），见 case-records spec「案件记录的存储」。数据库的错误原样抛出，不当作
    没有记录。
    """

    def case(self, case_id: UUID) -> CaseRecord | None:
        """这个案件最新的记录；没有时为 None。"""
        ...

    def save_case(self, record: CaseRecord) -> None:
        """保存这个案件最新的记录。"""
        ...

    def proposal(self, case_id: UUID) -> ProposalRecord | None:
        """这个案件最新的方案记录；没有时为 None。"""
        ...

    def save_proposal(self, proposal: ProposalRecord) -> None:
        """保存这个案件最新的方案记录。"""
        ...

    def findings(self, case_id: UUID) -> tuple[Finding, ...]:
        """这个案件的发现，按写入的顺序。"""
        ...

    def add_findings(self, case_id: UUID, findings: Sequence[Finding]) -> None:
        """按顺序写入这个案件的发现；这个案件已经有发现时不写。"""
        ...

    def recent_ended(self, symbol: str, limit: int) -> tuple[CaseRecord, ...]:
        """这只股票已结束的案件，按结束时刻从晚到早，时刻相同的按案件 ID，最多 limit 个。"""
        ...

    def last_concluded(self, case_type: CaseType, symbol: str | None) -> CaseRecord | None:
        """这种案件类型、这只股票（寻股为 None）有结论的案件里结论时刻最晚的一个；没有时为 None。"""
        ...
