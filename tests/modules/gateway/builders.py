"""Agent 网关测试的数据：signal-format spec「信号的格式」的基准数据，case-types spec 的结论（信号是
基准信号），task-dispatch spec「任务清单」的基准数据，以及 model-fallback spec「模型的配置」的基准
模型。
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIds
from tests.fakes.tasks import InMemoryTaskStore
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    AfterClose,
    CandidateConclusion,
    Decision,
    ExplorationConclusion,
    FollowUpReason,
    FollowUpRequest,
    HoldingConclusion,
    PlanDraft,
    PoolAddition,
    WatchConclusion,
)
from tradingagentspro.contracts.model import ModelRef, ModelUse
from tradingagentspro.contracts.signal import NoSignal, Signal
from tradingagentspro.contracts.task import (
    Dispatcher,
    Result,
    ResultStatus,
    Task,
    TaskFailure,
    TaskRejection,
    TaskRequest,
    TaskType,
)
from tradingagentspro.contracts.thesis import (
    Catalyst,
    CatalystReference,
    Comparison,
    Condition,
    ConditionKind,
    InvestmentThesis,
    ReferenceKind,
    WatchThesis,
)
from tradingagentspro.contracts.trade_plan import OrderType
from tradingagentspro.modules.gateway import (
    AgentModels,
    Attempt,
    CallError,
    CallStep,
    CaseScope,
    GatewayConfig,
    ModelAssignments,
    ModelFallbackService,
    TaskDispatchService,
    TaskRecord,
)


def at(hour: int, minute: int = 0, second: int = 0, *, day: int = 14) -> datetime:
    """2026 年 10 月某一天的某个 UTC 时刻，默认是 2026-10-14。"""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


NOW = at(15)  # 当前时刻：周三，盘中

_SIGNAL: dict[str, Any] = {
    "horizon": 5,
    "score": 1,
    "confidence": Decimal("0.6"),
    "data_as_of": at(14, 30),
    "cited_entries": ("E-1", "E-2"),
    "rationale": "订单回暖，放量突破前高",
    "risks": "板块整体回调时跟跌",
}


def signal(**changes: Any) -> Signal:
    """只把基准信号的这几项改为 changes 的信号，比如 signal(horizon=4)。"""
    return Signal.model_validate(_SIGNAL | changes)


SIGNAL = signal()  # 基准信号
NO_SIGNAL = NoSignal(reason="最近一期财报还没有进状态库，无法判断")  # 基准无信号
# 子 agent 交给网关的 JSON 文本
SIGNAL_JSON = SIGNAL.model_dump_json()
NO_SIGNAL_JSON = NO_SIGNAL.model_dump_json()


# 结论的公共部分里信号以外的字段：回答、理由、引用 R-1 和 R-2
_COMMON: dict[str, Any] = {
    "answer": "论点仍然成立",
    "rationale": "订单和毛利率都符合预期",
    "cited_results": ("R-1", "R-2"),
}


# case-types spec 的基准投资论点、关注论点、方案条款和跟进申请
INVESTMENT_THESIS = InvestmentThesis(
    rationale="新品周期带动收入加速",
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    target_price=Decimal(60),
    invalidation="毛利率连续两个季度下滑",
    invalidation_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(42)),
    ),
    review_day=date(2026, 10, 21),
)
WATCH_THESIS = WatchThesis(
    rationale="新品周期带动收入加速",
    entry_conditions=(
        Condition(kind=ConditionKind.PRICE, comparison=Comparison.AT_MOST, value=Decimal(50)),
    ),
    catalyst=Catalyst(description="Q3 财报", deadline=date(2026, 10, 29)),
    invalidation="毛利率连续两个季度下滑",
    review_day=date(2026, 10, 21),
)
PLAN = PlanDraft(  # 100 股，限价单，限价 50，止损价 45，紧急阈值 0.08
    quantity=100,
    order_type=OrderType.LIMIT,
    limit_price=Decimal(50),
    stop_price=Decimal(45),
    emergency_threshold=Decimal("0.08"),
)
FOLLOW_UP = FollowUpRequest(
    reason=FollowUpReason.EXTERNAL_SCHEDULE,
    reference=CatalystReference(kind=ReferenceKind.SCHEDULE, ref_id="S-9"),
    question="竞争对手 11 月 18 日发财报，之后对这只股票有什么影响？",
    possible_actions="减仓",
    trigger_at=datetime(2026, 11, 19, 14, 30, tzinfo=UTC),
)

# 每个决定必须附的内容：交易方案、投资论点、清仓后的去向、升级的关注论点
_ATTACHMENTS: dict[Decision, dict[str, Any]] = {
    Decision.ADD: {"plan": PLAN},
    Decision.REDUCE: {"plan": PlanDraft(quantity=60, order_type=OrderType.MARKET)},
    Decision.CLOSE: {
        "plan": PlanDraft(order_type=OrderType.MARKET),
        "after_close": AfterClose.REMOVE,
    },
    Decision.BUY: {"plan": PLAN, "thesis": INVESTMENT_THESIS},
    Decision.PROMOTE: {"watch_thesis": WATCH_THESIS},
}


def _common(signal: Signal, decision: Decision, changes: dict[str, Any]) -> dict[str, Any]:
    """结论的公共部分，信号是 signal，加上决定和它必须附的内容；changes 覆盖或补充其中的字段。"""
    return (
        _COMMON
        | {"signal": signal, "decision": decision}
        | _ATTACHMENTS.get(decision, {})
        | changes
    )


def holding(
    signal: Signal = SIGNAL, decision: Decision = Decision.HOLD, **changes: Any
) -> HoldingConclusion:
    """持仓的结论，默认是持有。"""
    return HoldingConclusion.model_validate(_common(signal, decision, changes))


def watch(
    signal: Signal = SIGNAL, decision: Decision = Decision.KEEP_WATCHING, **changes: Any
) -> WatchConclusion:
    """关注的结论，默认是继续关注。"""
    return WatchConclusion.model_validate(_common(signal, decision, changes))


def candidate(
    signal: Signal = SIGNAL, decision: Decision = Decision.KEEP, **changes: Any
) -> CandidateConclusion:
    """候选的结论，默认是保持。"""
    return CandidateConclusion.model_validate(_common(signal, decision, changes))


def ddd(signal: Signal = SIGNAL) -> PoolAddition:
    """寻股加入 DDD：理由"放量突破，论点草稿：订单回暖"。"""
    return PoolAddition(symbol="DDD", reason="放量突破，论点草稿：订单回暖", signal=signal)


def eee(signal: Signal = SIGNAL) -> PoolAddition:
    """寻股加入 EEE：理由"盈利预期上调"。"""
    return PoolAddition(symbol="EEE", reason="盈利预期上调", signal=signal)


def exploration(*additions: PoolAddition) -> ExplorationConclusion:
    """寻股的结论（没有自己的信号）：按顺序加入 additions，不删除。"""
    return ExplorationConclusion.model_validate(_COMMON | {"additions": additions})


# task-dispatch spec「任务清单」的基准数据

# 案件：K-1 是 BBB（关注）的关注股事件；K-2 是寻股；K-3 是 CCC（持仓）的持仓事件；K-4 是 BBB 的
# 关注复查，在 K-1 结束以后运行
K1 = CaseScope(UUID("00000000-0000-0000-0000-00000000c001"), CaseType.WATCH_EVENT, "BBB")
K2 = CaseScope(UUID("00000000-0000-0000-0000-00000000c002"), CaseType.EXPLORATION, None)
K3 = CaseScope(UUID("00000000-0000-0000-0000-00000000c003"), CaseType.HOLDING_EVENT, "CCC")
K4 = CaseScope(UUID("00000000-0000-0000-0000-00000000c004"), CaseType.WATCH_REVIEW, "BBB")

PRICE_REQUEST = TaskRequest(  # 价格请求
    task_type=TaskType.PRICE_REACTION,
    symbol="BBB",
    question="财报后的放量上涨能否持续？",
    inputs=("E-1",),
)
NEWS_REQUEST = TaskRequest(  # 新闻请求
    task_type=TaskType.EVENT_IMPACT,
    symbol="BBB",
    question="大客户续约对明年收入的影响有多大？",
    inputs=("E-2",),
)
EARNINGS_REQUEST = TaskRequest(  # 财报请求
    task_type=TaskType.EARNINGS_VS_EXPECTATIONS,
    symbol="BBB",
    question="毛利率超预期能否持续？",
    inputs=("E-3", "E-4"),
)

# 新生成的 Task 按先后依次是 T-1、T-2……：FakeIds 依次给出 UUID(int=1)、UUID(int=2)……
T1, T2, T3, T4, T5, T6, T7 = (UUID(int=n) for n in range(1, 8))
DEADLINE = at(15, 5)  # 当前时刻派发的任务的截止时间


def request(base: TaskRequest, **changes: Any) -> TaskRequest:
    """只把 base 的这几项改为 changes 的请求，比如 spec 里的"AAA 的价格请求"是
    request(PRICE_REQUEST, symbol="AAA")。
    """
    return TaskRequest.model_validate(base.model_dump() | changes)


def numbered(base: TaskRequest, n: int) -> TaskRequest:
    """spec 里的"价格请求 n""新闻请求 n"：问题改为"问题 n"。"""
    return request(base, question=f"问题 {n}")


def task(
    base: TaskRequest = PRICE_REQUEST,
    *,
    task_id: UUID = T1,
    case: CaseScope = K1,
    dispatcher: Dispatcher = Dispatcher.PM,
    pm_round: int | None = 1,
) -> Task:
    """直接构造的 Task：当前时刻派发，截止时间 15:05Z，默认是 PM 在 K-1 第 1 轮派发的价格请求
    T-1。Critic 的任务没有轮次，不用 pm_round。
    """
    return Task(
        task_id=task_id,
        case_id=case.case_id,
        dispatcher=dispatcher,
        task_type=base.task_type,
        symbol=base.symbol,
        question=base.question,
        inputs=base.inputs,
        dispatched_at=NOW,
        deadline=DEADLINE,
        pm_round=None if dispatcher is Dispatcher.CRITIC else pm_round,
    )


# model-fallback spec「模型的配置」的基准模型
P1 = ModelRef(vendor="anthropic", name="p-1")
P2 = ModelRef(vendor="anthropic", name="p-2")
P3 = ModelRef(vendor="google", name="p-3")
C1 = ModelRef(vendor="openai", name="c-1")
C2 = ModelRef(vendor="openai", name="c-2")
S1 = ModelRef(vendor="google", name="s-1")
S2 = ModelRef(vendor="openai", name="s-2")
S1_USE = ModelUse(model=S1, fallback=False)  # 输出出自 s-1，不是备用模型
S2_USE = ModelUse(model=S2, fallback=True)  # 输出出自 s-2，是备用模型

# 基准模型的配置：PM 是 p-1、p-2、p-3；Critic 是 c-1、c-2；三个子 agent 都是 s-1、s-2；Reviewer
# 只有 p-1，和 PM 的主模型是同一个模型
_SUB_AGENT_MODELS = AgentModels(primary=S1, fallbacks=(S2,))
_MODELS: dict[Agent, AgentModels] = {
    Agent.PM: AgentModels(primary=P1, fallbacks=(P2, P3)),
    Agent.NEWS: _SUB_AGENT_MODELS,
    Agent.QUANT: _SUB_AGENT_MODELS,
    Agent.FUNDAMENTALS: _SUB_AGENT_MODELS,
    Agent.CRITIC: AgentModels(primary=C1, fallbacks=(C2,)),
    Agent.REVIEWER: AgentModels(primary=P1),
}


def assignments(**changes: Any) -> ModelAssignments:
    """基准模型的配置，只把这几个 agent 的模型改为 changes，比如 assignments(pm={"primary": P1})。

    键是 agent 的值；值是 AgentModels 或者它的各项，为 None 时不配这个 agent。
    """
    agents: dict[str, Any] = {agent.value: models for agent, models in _MODELS.items()}
    agents |= changes
    present = {agent: models for agent, models in agents.items() if models is not None}
    return ModelAssignments.model_validate({"agents": present})


BASELINE_MODELS = assignments()

# 直接构造 Result 时各状态默认带的内容
_CONTENT: dict[ResultStatus, dict[str, Any]] = {
    ResultStatus.COMPLETED: {"output": SIGNAL, "model_use": S1_USE},
    ResultStatus.NO_SIGNAL: {"output": NO_SIGNAL, "model_use": S1_USE},
    ResultStatus.TIMED_OUT: {},
    ResultStatus.FAILED: {
        "reason": TaskFailure.FORMAT_INVALID,
        "detail": "horizon_not_offered",
        "model_use": S1_USE,
    },
    ResultStatus.REJECTED: {"reason": TaskRejection.NOT_CASE_STOCK},
}


def result(task_id: UUID, status: ResultStatus, **changes: Any) -> Result:
    """直接构造的 Result：完成是基准信号，无信号是基准无信号，失败是格式不合法，这三种的模型是
    s-1；被拒绝是股票不是案件的股票；耗时和费用都是 0。changes 覆盖其中的项。
    """
    fields = {"task_id": task_id, "status": status, "elapsed": timedelta(0), "cost": Decimal(0)}
    return Result.model_validate(fields | _CONTENT[status] | changes)


def record(task_: Task, status: ResultStatus | None = None) -> TaskRecord:
    """一个任务和它的 Result（见 result）；status 为 None 时还在运行，没有 Result。"""
    return TaskRecord(task_, None if status is None else result(task_.task_id, status))


def task_service(
    clock: FakeClock | None = None, store: InMemoryTaskStore | None = None, **config: Any
) -> TaskDispatchService:
    """任务派发用例：测试时钟（没有传入时停在当前时刻 15:00Z）、FakeIds、内存存储（没有传入时是
    空的）、默认配置。config 覆盖其中的配置项。
    """
    return TaskDispatchService(
        InMemoryTaskStore() if store is None else store,
        FakeClock(NOW) if clock is None else clock,
        FakeIds(),
        GatewayConfig(**config),
    )


def fallback_service(
    clock: FakeClock | None = None, models: ModelAssignments | None = None, **config: Any
) -> ModelFallbackService:
    """备用模型的用例：测试时钟（没有传入时停在当前时刻 15:00Z）、基准模型的配置（没有传入时）、
    默认配置。config 覆盖其中的配置项。
    """
    return ModelFallbackService(
        FakeClock(NOW) if clock is None else clock,
        BASELINE_MODELS if models is None else models,
        GatewayConfig(**config),
    )


def unavailable(
    service: ModelFallbackService, step: CallStep, error: CallError = CallError.TIMEOUT
) -> CallStep:
    """model-fallback spec 里的"某模型不可用（调用错误）"：正在第 1 次尝试的模型连续两次尝试都失败，
    调用错误都是 error（省略时是超时），返回之后的下一步。用默认的重试次数 1。
    """
    assert isinstance(step, Attempt) and step.call.attempt == 1
    retry = service.fail(step.call, error)
    assert isinstance(retry, Attempt) and (retry.call.model, retry.call.attempt) == (
        step.call.model,
        2,
    )
    return service.fail(retry.call, error)
