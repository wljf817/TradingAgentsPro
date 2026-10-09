"""Task 和 Result 格式的测试：任务清单、任务请求、Task、Result。

场景见 openspec 的 task-dispatch spec「任务清单」「任务请求的格式」「Task 的格式」
「Result 的格式」。基准数据和 spec 一致：派发时刻 2026-10-14 15:00Z，截止时间 15:05Z；Quant 的主模型
是 google/s-1，备用模型是 openai/s-2（model-fallback spec 的基准模型的配置）。
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from tradingagentspro.contracts.agent import SubAgent
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

NEWS, QUANT, FUNDAMENTALS = SubAgent.NEWS, SubAgent.QUANT, SubAgent.FUNDAMENTALS
COMPLETED, NO_SIGNAL_STATUS, TIMED_OUT, FAILED, REJECTED = (
    ResultStatus.COMPLETED,
    ResultStatus.NO_SIGNAL,
    ResultStatus.TIMED_OUT,
    ResultStatus.FAILED,
    ResultStatus.REJECTED,
)
DISPATCHED_AT = datetime(2026, 10, 14, 15, tzinfo=UTC)
DEADLINE = datetime(2026, 10, 14, 15, 5, tzinfo=UTC)
K1 = UUID("00000000-0000-0000-0000-00000000c001")
T1 = UUID(int=1)

# signal-format spec 的基准信号和基准无信号
SIGNAL = Signal(
    horizon=5,
    score=1,
    confidence=Decimal("0.6"),
    data_as_of=datetime(2026, 10, 14, 14, 30, tzinfo=UTC),
    cited_entries=("E-1", "E-2"),
    rationale="订单回暖，放量突破前高",
    risks="板块整体回调时跟跌",
)
NO_SIGNAL = NoSignal(reason="最近一期财报还没有进状态库，无法判断")
# 给出输出的模型：主模型 s-1，备用模型 s-2
S1_USE = ModelUse(model=ModelRef(vendor="google", name="s-1"), fallback=False)
S2_USE = ModelUse(model=ModelRef(vendor="openai", name="s-2"), fallback=True)

# 基准任务请求的各项
PRICE_REQUEST: dict[str, Any] = {
    "task_type": "price_reaction",
    "symbol": "BBB",
    "question": "财报后的放量上涨能否持续？",
    "inputs": ("E-1",),
}
NEWS_REQUEST: dict[str, Any] = {
    "task_type": "event_impact",
    "symbol": "BBB",
    "question": "大客户续约对明年收入的影响有多大？",
    "inputs": ("E-2",),
}
EARNINGS_REQUEST: dict[str, Any] = {
    "task_type": "earnings_vs_expectations",
    "symbol": "BBB",
    "question": "毛利率超预期能否持续？",
    "inputs": ("E-3", "E-4"),
}


def _task(**changes: Any) -> dict[str, Any]:
    """价格请求派发出去的 T-1 的各项：案件 K-1，PM 的第 1 轮；changes 覆盖其中的项。"""
    fields = PRICE_REQUEST | {
        "task_id": T1,
        "case_id": K1,
        "dispatcher": "pm",
        "dispatched_at": DISPATCHED_AT,
        "deadline": DEADLINE,
        "pm_round": 1,
    }
    return fields | changes


# 任务清单


@pytest.mark.parametrize(
    ("task_type", "agent"),
    [
        (TaskType.EVENT_IMPACT, NEWS),
        (TaskType.NEWS_SINCE, NEWS),
        (TaskType.PRICE_REACTION, QUANT),
        (TaskType.TREND_LEVELS, QUANT),
        (TaskType.VOLATILITY, QUANT),
        (TaskType.EARNINGS_VS_EXPECTATIONS, FUNDAMENTALS),
        (TaskType.GUIDANCE_CHANGE, FUNDAMENTALS),
        (TaskType.VALUATION, FUNDAMENTALS),
    ],
)
def test_target_of_each_task_type(task_type: TaskType, agent: SubAgent) -> None:
    """spec「每种任务类型的目标」。"""
    assert task_type.agent is agent


def test_task_types_are_the_eight_in_the_list() -> None:
    assert len(TaskType) == 8


def test_task_type_not_in_the_list() -> None:
    """spec「不在清单里的任务类型」：行业对比。"""
    with pytest.raises(ValidationError) as raised:
        TaskRequest.model_validate(PRICE_REQUEST | {"task_type": "industry_comparison"})

    assert [e["loc"] for e in raised.value.errors()] == [("task_type",)]


# 任务请求的格式


def test_complete_requests() -> None:
    """spec「完整的请求」：价格请求、新闻请求、财报请求；价格请求不写输入。"""
    price = TaskRequest.model_validate(PRICE_REQUEST)
    news = TaskRequest.model_validate(NEWS_REQUEST)
    earnings = TaskRequest.model_validate(EARNINGS_REQUEST)
    without_inputs = TaskRequest.model_validate(
        {k: v for k, v in PRICE_REQUEST.items() if k != "inputs"}
    )

    assert price == TaskRequest(
        task_type=TaskType.PRICE_REACTION,
        symbol="BBB",
        question="财报后的放量上涨能否持续？",
        inputs=("E-1",),
    )
    assert news.task_type is TaskType.EVENT_IMPACT
    assert earnings.inputs == ("E-3", "E-4")
    assert without_inputs.inputs == ()


@pytest.mark.parametrize("field", ["task_type", "symbol", "question"])
def test_request_missing_one_item(field: str) -> None:
    """spec「缺少一项或文字为空」：价格请求分别缺少任务类型、股票、问题，错误指出缺少的那一项。"""
    fields = dict(PRICE_REQUEST)
    del fields[field]

    with pytest.raises(ValidationError) as raised:
        TaskRequest.model_validate(fields)

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [("missing", (field,))]


@pytest.mark.parametrize(
    "changes",
    [{"question": "  "}, {"symbol": ""}, {"inputs": ("E-1", "  ")}],
    ids=["问题是空白", "股票是空字符串", "输入是 E-1 和空白"],
)
def test_request_with_empty_text(changes: dict[str, Any]) -> None:
    """spec「缺少一项或文字为空」：问题是 "  "；股票是 ""；输入是 E-1 和 "  "。"""
    with pytest.raises(ValidationError):
        TaskRequest.model_validate(PRICE_REQUEST | changes)


def test_request_text_is_stripped() -> None:
    request = TaskRequest.model_validate(PRICE_REQUEST | {"symbol": " BBB ", "inputs": (" E-1",)})

    assert (request.symbol, request.inputs) == ("BBB", ("E-1",))


def test_request_with_a_target() -> None:
    """spec「请求里写目标」：价格请求另外写了目标 Quant。"""
    with pytest.raises(ValidationError) as raised:
        TaskRequest.model_validate(PRICE_REQUEST | {"target": "quant"})

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [
        ("extra_forbidden", ("target",))
    ]


# Task 的格式（design D2）


def test_dispatched_task() -> None:
    """Task 带着请求的原样内容和网关填写的几项，目标是任务类型所属的子 agent。"""
    task = Task.model_validate(_task())

    assert task == Task(
        task_id=T1,
        case_id=K1,
        dispatcher=Dispatcher.PM,
        task_type=TaskType.PRICE_REACTION,
        symbol="BBB",
        question="财报后的放量上涨能否持续？",
        inputs=("E-1",),
        dispatched_at=DISPATCHED_AT,
        deadline=DEADLINE,
        pm_round=1,
    )
    assert task.target is QUANT


@pytest.mark.parametrize("request_", [NEWS_REQUEST, EARNINGS_REQUEST], ids=["新闻", "财报"])
def test_task_target_follows_the_task_type(request_: dict[str, Any]) -> None:
    task = Task.model_validate(_task(**request_))

    assert task.target is TaskType(request_["task_type"]).agent


def test_critic_and_rejected_tasks_have_no_round() -> None:
    """Critic 的任务和被拒绝的 PM 任务都没有 PM 的轮次。"""
    critic = Task.model_validate(_task(dispatcher="critic", pm_round=None))
    rejected = Task.model_validate(_task(pm_round=None))

    assert (critic.dispatcher, critic.pm_round) == (Dispatcher.CRITIC, None)
    assert rejected.pm_round is None


@pytest.mark.parametrize("dispatcher", ["news", "reviewer"])
def test_only_pm_and_critic_dispatch(dispatcher: str) -> None:
    """spec「只有 PM 和 Critic 能派任务」：发起者是 News；或者是 Reviewer，都报错。"""
    with pytest.raises(ValidationError) as raised:
        Task.model_validate(_task(dispatcher=dispatcher))

    assert [e["loc"] for e in raised.value.errors()] == [("dispatcher",)]


@pytest.mark.parametrize(
    "deadline", [DISPATCHED_AT, datetime(2026, 10, 14, 14, 59, tzinfo=UTC)], ids=["等于", "早于"]
)
def test_deadline_not_after_dispatch(deadline: datetime) -> None:
    """截止时间必须晚于派发时刻。"""
    with pytest.raises(ValidationError):
        Task.model_validate(_task(deadline=deadline))


@pytest.mark.parametrize(
    "changes",
    [{"dispatcher": "critic", "pm_round": 1}, {"pm_round": 0}],
    ids=["Critic 的任务带轮次", "第 0 轮"],
)
def test_invalid_round(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Task.model_validate(_task(**changes))


def test_naive_times_are_errors() -> None:
    naive = datetime(2026, 10, 14, 15)  # noqa: DTZ001

    with pytest.raises(ValidationError):
        Task.model_validate(_task(dispatched_at=naive))


@pytest.mark.parametrize(("field", "value"), [("symbol", "AAA"), ("pm_round", 2)])
def test_task_cannot_be_changed(field: str, value: object) -> None:
    """spec「Task 的格式」：Task 生成以后不能修改，赋值报错，Task 不变。"""
    task = Task.model_validate(_task())

    with pytest.raises(ValidationError):
        setattr(task, field, value)

    assert task == Task.model_validate(_task())


# Result 的格式


def _result(status: ResultStatus, **changes: Any) -> Result:
    """T-1 的 Result：耗时 90 秒，费用 0.02；changes 覆盖或补充其中的项。"""
    fields = {
        "task_id": T1,
        "status": status,
        "elapsed": timedelta(seconds=90),
        "cost": Decimal("0.02"),
    }
    return Result.model_validate(fields | changes)


@pytest.mark.parametrize(
    ("status", "content", "cited"),
    [
        (COMPLETED, {"output": SIGNAL, "model_use": S1_USE}, ("E-1", "E-2")),
        (NO_SIGNAL_STATUS, {"output": NO_SIGNAL, "model_use": S1_USE}, ()),
        (
            NO_SIGNAL_STATUS,
            {
                "output": NO_SIGNAL.model_copy(update={"cited_entries": ("E-1",)}),
                "model_use": S2_USE,
            },
            ("E-1",),
        ),
        (TIMED_OUT, {}, ()),
        (
            FAILED,
            {
                "reason": TaskFailure.FORMAT_INVALID,
                "detail": "horizon_not_offered",
                "model_use": S1_USE,
            },
            (),
        ),
        (FAILED, {"reason": TaskFailure.CALL_FAILED, "detail": "主模型和备用模型都失败"}, ()),
        (
            REJECTED,
            {
                "reason": TaskRejection.ROUNDS_USED_UP,
                "elapsed": timedelta(0),
                "cost": Decimal(0),
            },
            (),
        ),
    ],
    ids=[
        "完成",
        "无信号",
        "无信号引用了 E-1",
        "超时",
        "失败：格式不合法",
        "失败：调用失败",
        "被拒绝",
    ],
)
def test_result_of_each_status(
    status: ResultStatus, content: dict[str, Any], cited: tuple[str, ...]
) -> None:
    """spec「Result 的格式」表的每一种状态；引用的条目取自输出，没有输出时为空。spec「完成和无信号的
    Result」：完成和无信号的模型是 s-1、不是备用模型。
    """
    result = _result(status, **content)

    assert result.status is status
    assert result.cited_entries == cited
    assert result.model_use == content.get("model_use")


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (COMPLETED, {"model_use": S1_USE}),
        (COMPLETED, {"output": NO_SIGNAL, "model_use": S1_USE}),
        (NO_SIGNAL_STATUS, {"output": SIGNAL, "model_use": S1_USE}),
        (TIMED_OUT, {"output": SIGNAL}),
        (FAILED, {"detail": "格式不合法"}),
        (FAILED, {"reason": TaskFailure.FORMAT_INVALID, "model_use": S1_USE}),
        (REJECTED, {"reason": TaskFailure.FORMAT_INVALID}),
        (
            NO_SIGNAL_STATUS,
            {"output": NO_SIGNAL, "reason": TaskRejection.ROUNDS_USED_UP, "model_use": S1_USE},
        ),
        (COMPLETED, {"output": SIGNAL, "cost": Decimal("-0.01"), "model_use": S1_USE}),
    ],
    ids=[
        "完成，没有输出",
        "完成，输出是基准无信号",
        "无信号，输出是基准信号",
        "超时，输出是基准信号",
        "失败，没有原因",
        "失败，原因是格式不合法，没有说明",
        "被拒绝，原因是格式不合法",
        "无信号，原因是轮次已用完",
        "完成，费用 -0.01",
    ],
)
def test_status_and_content_do_not_match(status: ResultStatus, content: dict[str, Any]) -> None:
    """spec「状态和内容不符」：都报错。该带模型的带上 s-1，只有一处不符。"""
    with pytest.raises(ValidationError):
        _result(status, **content)


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (COMPLETED, {"output": SIGNAL}),
        (NO_SIGNAL_STATUS, {"output": NO_SIGNAL}),
        (FAILED, {"reason": TaskFailure.FORMAT_INVALID, "detail": "horizon_not_offered"}),
        (TIMED_OUT, {"model_use": S1_USE}),
        (
            FAILED,
            {"reason": TaskFailure.CALL_FAILED, "detail": "模型都不可用", "model_use": S1_USE},
        ),
        (REJECTED, {"reason": TaskRejection.NOT_CASE_STOCK, "model_use": S1_USE}),
    ],
    ids=[
        "完成，没有模型",
        "无信号，没有模型",
        "失败，原因是格式不合法，没有模型",
        "超时，模型是 s-1",
        "失败，原因是调用失败，模型是 s-1",
        "被拒绝，模型是 s-1",
    ],
)
def test_model_and_status_do_not_match(status: ResultStatus, content: dict[str, Any]) -> None:
    """spec「模型和状态不符」：都报错。"""
    with pytest.raises(ValidationError, match="给出输出的模型"):
        _result(status, **content)


def test_format_invalid_failure_from_a_fallback_model() -> None:
    """spec「模型和状态不符」：失败，原因是格式不合法，说明"评估窗口不在配置的选项里"，模型是 s-2、
    是备用模型，格式正确。
    """
    result = _result(
        FAILED,
        reason=TaskFailure.FORMAT_INVALID,
        detail="评估窗口不在配置的选项里",
        model_use=S2_USE,
    )

    assert result.model_use == ModelUse(model=ModelRef(vendor="openai", name="s-2"), fallback=True)


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (REJECTED, {"reason": TaskRejection.NOT_CASE_STOCK, "detail": "股票不是案件的股票"}),
        (TIMED_OUT, {"reason": TaskFailure.CALL_FAILED}),
        (FAILED, {"reason": TaskFailure.CALL_FAILED, "detail": "  "}),
    ],
    ids=["被拒绝带说明", "超时带原因", "失败的说明是空白"],
)
def test_other_mismatched_content(status: ResultStatus, content: dict[str, Any]) -> None:
    """只有失败带说明，说明不能为空；超时不带原因。"""
    with pytest.raises(ValidationError):
        _result(status, **content)


def test_negative_elapsed_is_an_error() -> None:
    """耗时小于 0 报错。"""
    with pytest.raises(ValidationError):
        _result(TIMED_OUT, elapsed=timedelta(seconds=-1))


def test_result_cannot_be_changed() -> None:
    result = _result(COMPLETED, output=SIGNAL, model_use=S1_USE)

    with pytest.raises(ValidationError):
        result.status = TIMED_OUT  # type: ignore[misc]

    assert result == _result(COMPLETED, output=SIGNAL, model_use=S1_USE)


# 写出再读回（design D12）

_texts = st.text(min_size=1, max_size=20).filter(str.strip)
_entries = st.lists(_texts, max_size=3).map(tuple)
_offsets = st.integers(-(23 * 60 + 59), 23 * 60 + 59).map(
    lambda minutes: timezone(timedelta(minutes=minutes))
)
_moments = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001
    timezones=_offsets,
)
_signals = st.builds(
    Signal,
    horizon=st.integers(min_value=1, max_value=250),
    score=st.integers(min_value=-2, max_value=2),
    confidence=st.decimals(min_value=0, max_value=1, places=4),
    data_as_of=_moments,
    cited_entries=st.lists(_texts, min_size=1, max_size=3).map(tuple),
    rationale=_texts,
    risks=_texts,
)
_no_signals = st.builds(NoSignal, reason=_texts, cited_entries=_entries)
_elapsed = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=1))
_costs = st.decimals(min_value=0, max_value=100, places=4)
_names = st.text(alphabet="abcXYZ-.0123", min_size=1, max_size=10)
_model_uses = st.builds(
    ModelUse,
    model=st.builds(ModelRef, vendor=_names, name=_names),
    fallback=st.booleans(),
)


@st.composite
def _tasks(draw: st.DrawFn) -> Task:
    """任意合法的 Task：截止时间晚于派发时刻，Critic 的任务没有轮次。"""
    dispatcher = draw(st.sampled_from(Dispatcher))
    dispatched_at = draw(_moments)
    timeout = draw(st.timedeltas(min_value=timedelta(microseconds=1), max_value=timedelta(days=1)))
    pm_round = None if dispatcher is Dispatcher.CRITIC else draw(st.none() | st.integers(1, 9))
    return Task(
        task_id=draw(st.uuids()),
        case_id=draw(st.uuids()),
        dispatcher=dispatcher,
        task_type=draw(st.sampled_from(TaskType)),
        symbol=draw(_texts),
        question=draw(_texts),
        inputs=draw(_entries),
        dispatched_at=dispatched_at,
        deadline=dispatched_at + timeout,
        pm_round=pm_round,
    )


_results = st.one_of(
    st.builds(
        Result,
        task_id=st.uuids(),
        status=st.just(COMPLETED),
        output=_signals,
        elapsed=_elapsed,
        cost=_costs,
        model_use=_model_uses,
    ),
    st.builds(
        Result,
        task_id=st.uuids(),
        status=st.just(NO_SIGNAL_STATUS),
        output=_no_signals,
        elapsed=_elapsed,
        cost=_costs,
        model_use=_model_uses,
    ),
    st.builds(Result, task_id=st.uuids(), status=st.just(TIMED_OUT), elapsed=_elapsed, cost=_costs),
    st.builds(
        Result,
        task_id=st.uuids(),
        status=st.just(FAILED),
        reason=st.just(TaskFailure.FORMAT_INVALID),
        detail=_texts,
        elapsed=_elapsed,
        cost=_costs,
        model_use=_model_uses,
    ),
    st.builds(
        Result,
        task_id=st.uuids(),
        status=st.just(FAILED),
        reason=st.just(TaskFailure.CALL_FAILED),
        detail=_texts,
        elapsed=_elapsed,
        cost=_costs,
    ),
    st.builds(
        Result,
        task_id=st.uuids(),
        status=st.just(REJECTED),
        reason=st.sampled_from(TaskRejection),
        elapsed=st.just(timedelta(0)),
        cost=st.just(Decimal(0)),
    ),
)


@given(task=_tasks())
def test_task_survives_a_json_round_trip(task: Task) -> None:
    """任意合法的 Task 按 JSON 写出再读回，对象相等。"""
    assert Task.model_validate_json(task.model_dump_json()) == task


@given(result=_results)
def test_result_survives_a_json_round_trip(result: Result) -> None:
    """任意合法的五种状态的 Result 按 JSON 写出再读回，对象相等；输出、原因和模型读回同样的类型。"""
    restored = Result.model_validate_json(result.model_dump_json())

    assert restored == result
    assert type(restored.output) is type(result.output)
    assert type(restored.reason) is type(result.reason)
    assert type(restored.model_use) is type(result.model_use)
