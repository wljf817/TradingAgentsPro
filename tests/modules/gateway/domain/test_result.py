"""子 agent 的输出、调用失败、超时和被拒绝变成 Result 的测试，直接构造 Task。

规则见 openspec 的 task-dispatch spec「子 agent 的输出变成 Result」「超时」「调用失败」「Result 的
格式」；JSON 文本经用例解析的场景见 tests/modules/gateway/test_service.py。T-1 是 PM 派发的价格
请求：派发时刻 2026-10-14 15:00Z，截止时间 15:05Z。
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tests.modules.gateway.builders import NO_SIGNAL, SIGNAL, T1, at, signal, task
from tradingagentspro.contracts.signal import SubAgentOutput
from tradingagentspro.contracts.task import Result, ResultStatus, TaskFailure, TaskRejection
from tradingagentspro.modules.gateway import (
    GatewayConfig,
    Malformed,
    SignalRejection,
    call_failed,
    expired,
    received,
    rejected,
)

COMPLETED, NO_SIGNAL_STATUS, TIMED_OUT, FAILED, REJECTED = (
    ResultStatus.COMPLETED,
    ResultStatus.NO_SIGNAL,
    ResultStatus.TIMED_OUT,
    ResultStatus.FAILED,
    ResultStatus.REJECTED,
)
FORMAT_INVALID, CALL_FAILED = TaskFailure.FORMAT_INVALID, TaskFailure.CALL_FAILED
CONFIG = GatewayConfig()
T1_TASK = task()
COST = Decimal("0.02")
RECEIVED_AT = at(15, 1, 30)
MISSING_RISKS = Malformed("Signal.risks: Field required; NoSignal.reason: Field required")


def _received(output: SubAgentOutput | Malformed, now: datetime = RECEIVED_AT) -> Result:
    return received(T1_TASK, output, now, COST, CONFIG)


# 子 agent 的输出变成 Result


def test_valid_signal_is_completed() -> None:
    """spec「收到合法的信号和无信号」：完成，输出是基准信号；耗时 90 秒，费用原样。"""
    assert _received(SIGNAL) == Result(
        task_id=T1,
        status=COMPLETED,
        output=SIGNAL,
        elapsed=timedelta(seconds=90),
        cost=COST,
    )


def test_valid_no_signal() -> None:
    """spec「收到合法的信号和无信号」：无信号，输出是基准无信号。"""
    assert _received(NO_SIGNAL) == Result(
        task_id=T1,
        status=NO_SIGNAL_STATUS,
        output=NO_SIGNAL,
        elapsed=timedelta(seconds=90),
        cost=COST,
    )


def test_malformed_output_fails() -> None:
    """spec「结构错误」：失败，原因是格式不合法，说明是 Malformed 的说明；不带输出。"""
    assert _received(MISSING_RISKS) == Result(
        task_id=T1,
        status=FAILED,
        reason=FORMAT_INVALID,
        detail="Signal.risks: Field required; NoSignal.reason: Field required",
        elapsed=timedelta(seconds=90),
        cost=COST,
    )


@pytest.mark.parametrize(
    ("output", "detail"),
    [
        (signal(horizon=4), SignalRejection.HORIZON_NOT_OFFERED),
        (signal(data_as_of=at(15, 1, 31)), SignalRejection.DATA_AFTER_NOW),
    ],
    ids=["评估窗口 4", "数据截至 15:01:31Z"],
)
def test_signal_rejected_by_the_checks_fails(output: SubAgentOutput, detail: str) -> None:
    """spec「评估窗口不在配置的选项里」「数据截至时刻晚于收到的时刻」：失败，原因是格式不合法，
    说明是 signal-format 给出的原因。
    """
    result = _received(output)

    assert (result.status, result.reason, result.detail) == (FAILED, FORMAT_INVALID, detail)
    assert result.output is None


def test_data_as_of_equal_to_now_is_completed() -> None:
    """spec「数据截至时刻晚于收到的时刻」：数据截至 15:01:30Z 的信号完成。"""
    result = _received(signal(data_as_of=RECEIVED_AT))

    assert result.status is COMPLETED


def test_other_horizon_options() -> None:
    """配置的评估窗口选项改为 5 和 20：评估窗口 3 的信号失败。"""
    config = GatewayConfig(signal_horizons=(5, 20))

    result = received(T1_TASK, signal(horizon=3), RECEIVED_AT, COST, config)

    assert (result.status, result.detail) == (FAILED, SignalRejection.HORIZON_NOT_OFFERED)


# 超时


def test_output_at_the_deadline_is_handled() -> None:
    """spec「截止时间的边界」：15:05:00Z 收到基准信号，完成；耗时 5 分钟。"""
    result = _received(SIGNAL, at(15, 5))

    assert (result.status, result.output) == (COMPLETED, SIGNAL)
    assert result.elapsed == timedelta(minutes=5)


@pytest.mark.parametrize(
    "output",
    [SIGNAL, NO_SIGNAL, MISSING_RISKS, signal(horizon=4)],
    ids=["基准信号", "基准无信号", "结构错误", "评估窗口 4"],
)
def test_output_after_the_deadline_times_out(output: SubAgentOutput | Malformed) -> None:
    """spec「截止时间的边界」：15:05:01Z 收到，不论输出合不合法都是超时，没有输出。"""
    assert _received(output, at(15, 5, 1)) == Result(
        task_id=T1,
        status=TIMED_OUT,
        elapsed=timedelta(minutes=5, seconds=1),
        cost=COST,
    )


def test_expire_before_the_deadline_is_an_error() -> None:
    """spec「截止时间以前不能记超时」：15:04:59Z 记超时报错。"""
    with pytest.raises(ValueError, match="还没到截止时间"):
        expired(T1_TASK, at(15, 4, 59), Decimal("0.01"))


@pytest.mark.parametrize(
    ("now", "elapsed"),
    [(at(15, 5), timedelta(minutes=5)), (at(15, 7, 30), timedelta(minutes=7, seconds=30))],
    ids=["15:05:00Z", "15:07:30Z"],
)
def test_expire_at_or_after_the_deadline(now: datetime, elapsed: timedelta) -> None:
    """spec「到截止时间仍没有输出」：超时，耗时是记超时的时刻减派发时刻，费用原样。"""
    assert expired(T1_TASK, now, Decimal("0.01")) == Result(
        task_id=T1, status=TIMED_OUT, elapsed=elapsed, cost=Decimal("0.01")
    )


# 调用失败


@pytest.mark.parametrize(
    ("now", "elapsed"),
    [(at(15, 2), timedelta(minutes=2)), (at(15, 5), timedelta(minutes=5))],
    ids=["15:02:00Z", "截止时间"],
)
def test_call_failed(now: datetime, elapsed: timedelta) -> None:
    """spec「调用失败」：失败，原因是调用失败，说明原样，耗时 2 分钟；截止时间当时也是失败。"""
    assert call_failed(T1_TASK, "主模型和备用模型都失败", now, Decimal(0)) == Result(
        task_id=T1,
        status=FAILED,
        reason=CALL_FAILED,
        detail="主模型和备用模型都失败",
        elapsed=elapsed,
        cost=Decimal(0),
    )


def test_call_failed_after_the_deadline_times_out() -> None:
    """spec「截止时间以后的调用失败」：15:05:01Z 给出调用失败，超时。"""
    result = call_failed(T1_TASK, "主模型和备用模型都失败", at(15, 5, 1), Decimal("0.01"))

    assert (result.status, result.reason, result.detail) == (TIMED_OUT, None, None)
    assert result.cost == Decimal("0.01")


# 被拒绝


@pytest.mark.parametrize("reason", list(TaskRejection))
def test_rejected(reason: TaskRejection) -> None:
    """spec「Result 的格式」：被拒绝的耗时和费用都是 0，原因原样。"""
    assert rejected(T1_TASK, reason) == Result(
        task_id=T1, status=REJECTED, reason=reason, elapsed=timedelta(0), cost=Decimal(0)
    )
