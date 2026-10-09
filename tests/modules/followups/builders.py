"""跟进审批测试的数据：follow-ups spec「跟进的记录」里的基准数据。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tests.fakes.follow_ups import InMemoryFollowUpStore
from tests.fakes.ids import FakeIds
from tradingagentspro.contracts.conclusion import FollowUpReason, FollowUpRequest
from tradingagentspro.contracts.thesis import (
    Catalyst,
    CatalystReference,
    Comparison,
    Condition,
    ConditionKind,
    ReferenceKind,
)
from tradingagentspro.modules.followups import (
    FollowUp,
    FollowUpConfig,
    FollowUpService,
    FollowUpStatus,
)

NYSE = sample_nyse_calendar()


def at(
    hour: int, minute: int = 0, second: int = 0, *, day: int = 14, month: int = 10, year: int = 2026
) -> datetime:
    """某一天的某个 UTC 时刻，默认是基准交易日 2026-10-14。"""
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


BASELINE_NOW = at(15)  # 周三，盘中；常规时段 13:30Z 到 20:00Z
DAY_START = at(20, day=13)  # 2026-10-14 这个交易日开始的时刻：上一个交易日的收盘

# 引用的对象
E1 = CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="E-1")
S9 = CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="S-9")
S10 = CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="S-10")
V7 = CatalystReference(kind=ReferenceKind.EVENT, ref_id="V-7")
EVENT_E1 = CatalystReference(kind=ReferenceKind.EVENT, ref_id="E-1")  # ID 和日程项 E-1 相同
PRICE_55 = Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_LEAST, value=Decimal(55))
PRICE_56 = Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_LEAST, value=Decimal(56))

# 审批时传入的催化剂。截止日期 spec 没有写，取 stock-pool spec 基准关注论点的
CCC_CATALYST = Catalyst(description="Q3 财报", deadline=date(2026, 10, 29), reference=E1)
BBB_CATALYST = Catalyst(description="Q3 财报", deadline=date(2026, 10, 29))
CATALYSTS = {"CCC": CCC_CATALYST, "BBB": BBB_CATALYST}

# 日程申请：case-types spec 的基准跟进申请
SCHEDULE_REQUEST = FollowUpRequest(
    reason=FollowUpReason.EXTERNAL_SCHEDULE,
    reference=S9,
    question="竞争对手 11 月 18 日发财报，之后对这只股票有什么影响？",
    possible_actions="减仓",
    trigger_at=at(14, 30, day=19, month=11),
)
EVENT_REQUEST = FollowUpRequest(
    reason=FollowUpReason.EVENT_REACTION,
    reference=V7,
    question="大单公布 3 小时后盘中反应如何？",
    possible_actions="加仓",
    trigger_at=at(18),
)
OBSERVATION_REQUEST = FollowUpRequest(
    reason=FollowUpReason.OBSERVATION,
    reference=PRICE_55,
    question="站上 55 以后能不能守住？",
    possible_actions="加仓",
    trigger_at=at(20, day=21),
)

# 来源案件
K1 = UUID("00000000-0000-0000-0000-00000000c001")
K2 = UUID("00000000-0000-0000-0000-00000000c002")

# 获批的跟进按先后依次是 F-1、F-2……：FakeIds 依次给出 UUID(int=1)、UUID(int=2)……
F1, F2, F3, F4 = (UUID(int=n) for n in range(1, 5))


def changed(request: FollowUpRequest, **update: Any) -> FollowUpRequest:
    """spec 里的"触发时间改为 X""引用改为 X"：只把申请的这几项改为新值。"""
    return request.model_copy(update=update)


def follow_up(
    request: FollowUpRequest = SCHEDULE_REQUEST,
    *,
    follow_up_id: UUID = F1,
    symbol: str = "CCC",
    case_id: UUID = K1,
    requested_at: datetime = BASELINE_NOW,
    status: FollowUpStatus = FollowUpStatus.PENDING,
) -> FollowUp:
    """直接构造的跟进，默认是 CCC 的案件 K-1 在基准时刻获批的日程申请 F-1。"""
    return FollowUp(follow_up_id, symbol, request, case_id, requested_at, status)


def follow_up_service(
    clock: FakeClock | None = None, store: InMemoryFollowUpStore | None = None, **config: Any
) -> FollowUpService:
    """跟进审批用例：测试时钟（没有传入时停在基准时刻）、FakeIds、纽交所样例日历、内存存储
    （没有传入时是空的）、默认配置。

    config 覆盖其中的配置项。
    """
    return FollowUpService(
        InMemoryFollowUpStore() if store is None else store,
        FakeClock(BASELINE_NOW) if clock is None else clock,
        NYSE,
        FakeIds(),
        FollowUpConfig(**config),
    )
