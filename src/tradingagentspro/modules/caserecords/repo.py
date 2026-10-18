"""案件记录的表：案件表 caserecords_cases、方案表 caserecords_proposals、发现表
caserecords_findings，以及 CaseRecordStore 的数据库实现。

案件表和方案表是状态库的版本表，键是案件 ID；发现表一项一行、只追加。见 case-records spec
「案件记录的存储」和 add-case-records 的 design D1、D5、D6。
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Uuid,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.modules.caserecords.domain.record import (
    CaseRecord,
    CaseStatus,
    Finding,
    ProposalRecord,
)
from tradingagentspro.modules.statestore import VersionedStore, versioned_table
from tradingagentspro.shared.clock import Clock

# symbol、case_type、status、concluded 按值筛，concluded_at、ended_at 排序（design D6）
CASES = versioned_table(
    "caserecords_cases",
    Column("case_id", Uuid, nullable=False),
    Column("symbol", String, nullable=True),
    Column("case_type", String, nullable=True),
    Column("status", String, nullable=False),
    Column("concluded", Boolean, nullable=False),
    Column("concluded_at", DateTime(timezone=True), nullable=True),
    Column("ended_at", DateTime(timezone=True), nullable=True),
)

PROPOSALS = versioned_table("caserecords_proposals", Column("case_id", Uuid, nullable=False))

# seq 是这个案件的第几项发现，从 1 起，按写入的顺序
FINDINGS = Table(
    "caserecords_findings",
    MetaData(),
    Column("case_id", Uuid, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("symbol", String, nullable=False),
    Column("data", JSONB, nullable=False),
    PrimaryKeyConstraint("case_id", "seq"),
)

_FINDING = TypeAdapter(Finding)


class PostgresCaseRecordStore:
    """满足 CaseRecordStore：案件记录和方案记录各用一个 VersionedStore，保存时从记录算出筛选列；
    发现直接写发现表。每个操作一个事务；数据库的错误、读不出来的记录原样抛出。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._cases = VersionedStore(engine, clock, CASES, CaseRecord)
        self._proposals = VersionedStore(engine, clock, PROPOSALS, ProposalRecord)

    def case(self, case_id: UUID) -> CaseRecord | None:
        return self._cases.get(case_id)

    def save_case(self, record: CaseRecord) -> None:
        conclusion = record.conclusion
        self._cases.save(
            record.case_id,
            record,
            symbol=record.trigger.symbol,
            case_type=None if record.case_type is None else record.case_type.value,
            status=record.status.value,
            concluded=conclusion is not None,
            concluded_at=None if conclusion is None else conclusion.at,
            ended_at=record.ended_at,
        )

    def proposal(self, case_id: UUID) -> ProposalRecord | None:
        return self._proposals.get(case_id)

    def save_proposal(self, proposal: ProposalRecord) -> None:
        self._proposals.save(proposal.case_id, proposal)

    def findings(self, case_id: UUID) -> tuple[Finding, ...]:
        query = (
            select(FINDINGS.c.data).where(FINDINGS.c.case_id == case_id).order_by(FINDINGS.c.seq)
        )
        with self._engine.connect() as connection:
            rows: Sequence[Any] = connection.execute(query).scalars().all()
        return tuple(_FINDING.validate_python(data) for data in rows)

    def add_findings(self, case_id: UUID, findings: Sequence[Finding]) -> None:
        """同一个事务里先看这个案件有没有发现，没有才按顺序插入（design D5）。"""
        existing = select(FINDINGS.c.seq).where(FINDINGS.c.case_id == case_id).limit(1)
        with self._engine.begin() as connection:
            if connection.execute(existing).first() is not None:
                return
            for seq, finding in enumerate(findings, start=1):
                connection.execute(
                    insert(FINDINGS).values(
                        case_id=case_id,
                        seq=seq,
                        symbol=finding.symbol,
                        data=_FINDING.dump_python(finding, mode="json"),
                    )
                )

    def recent_ended(self, symbol: str, limit: int) -> tuple[CaseRecord, ...]:
        return self._cases.most_recent(
            "ended_at", limit, symbol=symbol, status=CaseStatus.ENDED.value
        )

    def last_concluded(self, case_type: CaseType, symbol: str | None) -> CaseRecord | None:
        found = self._cases.most_recent(
            "concluded_at", 1, case_type=case_type.value, symbol=symbol, concluded=True
        )
        return found[0] if found else None
