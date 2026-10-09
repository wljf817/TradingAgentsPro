"""备用模型用例的测试。每个测试对应 openspec 的 model-fallback spec 里的一个场景，说明里写明场景
（「模型的配置」「厂商校验」和「备用模型的配置」的前三个场景在 test_config.py）。

基准数据见 tests/modules/gateway/builders.py：当前时刻 2026-10-14 15:00Z，基准模型的配置；K-1、K-2
是固定的 UUID。"News 在 K-1 为 T-1 调用"带着 T-1 的截止时间 15:05Z，PM、Critic、Reviewer 的调用没有
截止时间。"p-1 不可用（限流）"是 builders 的 unavailable：连续两次尝试都失败。spec 一个 WHEN 里用
"；"或"或者"隔开的几种情况各自从 GIVEN 开始，测试里各用一个新的用例。
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import UUID

import pytest

from tests.fakes.clock import FakeClock
from tests.modules.gateway.builders import (
    C1,
    C2,
    DEADLINE,
    K1,
    K2,
    NEWS_REQUEST,
    NOW,
    P1,
    P2,
    P3,
    S1,
    S1_USE,
    S2,
    at,
    fallback_service,
    task_service,
    unavailable,
    watch,
)
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.conclusion import Decision
from tradingagentspro.contracts.model import ModelRef, ModelUse
from tradingagentspro.contracts.task import Dispatcher, ResultStatus, TaskFailure
from tradingagentspro.modules.gateway import (
    AllUnavailable,
    Attempt,
    CallError,
    CallStep,
    DeadlineReached,
    GatewayConfig,
    ModelCall,
    ModelSwitch,
    Unavailable,
    blocked_decision,
)

PM, NEWS, QUANT = Agent.PM, Agent.NEWS, Agent.QUANT
CRITIC, REVIEWER = Agent.CRITIC, Agent.REVIEWER
SUB_AGENTS = (NEWS, QUANT, Agent.FUNDAMENTALS)
K_1, K_2 = K1.case_id, K2.case_id
TIMEOUT, CONNECTION, RATE_LIMITED = CallError.TIMEOUT, CallError.CONNECTION, CallError.RATE_LIMITED
PM_TIMEOUT, NEWS_TIMEOUT = timedelta(seconds=180), timedelta(seconds=60)


def _use(model: ModelRef, fallback: bool) -> ModelUse:
    return ModelUse(model=model, fallback=fallback)


class _Setup:
    """一个备用模型的用例和它的时钟。"""

    def __init__(self, **config: Any) -> None:
        self.clock = FakeClock(NOW)
        self.config = GatewayConfig(**config)
        self.service = fallback_service(self.clock, **config)
        # 熔断状态的辅助步骤用的案件，和 K-1、K-2 不同
        self._spare: Iterator[UUID] = (UUID(int=0xF000 + n) for n in count(1))

    def at(self, moment: datetime) -> "_Setup":
        self.clock.set(moment)
        return self

    def begin(self, agent: Agent = PM, case: UUID = K_1) -> CallStep:
        """spec 里的"PM 在 K-1 调用"：子 agent 的调用带着 T-1 的截止时间。"""
        deadline = DEADLINE if agent in SUB_AGENTS else None
        return self.service.begin(case, agent, deadline)

    def attempt(self, agent: Agent = PM, case: UUID = K_1) -> Attempt:
        """开始一次调用，第一步是一次尝试。"""
        step = self.begin(agent, case)
        assert isinstance(step, Attempt)
        return step

    def unavailable(self, step: CallStep, error: CallError = TIMEOUT) -> CallStep:
        return unavailable(self.service, step, error)

    def succeed(self, step: CallStep) -> ModelUse:
        assert isinstance(step, Attempt)
        return self.service.succeed(step.call)

    def fallback_used(self, agent: Agent = PM, case: UUID = K_1) -> ModelUse:
        """spec 里的"主模型不可用，第一个备用模型成功"：返回实际使用的模型。"""
        return self.succeed(self.unavailable(self.attempt(agent, case)))

    def unavailable_at(self, agent: Agent, *moments: datetime, case: UUID = K_1) -> None:
        """spec 里的"p-1 在 t 不可用"：在每个时刻为 case 的 agent 开始一次调用，第一次尝试的模型
        不可用。
        """
        for moment in moments:
            self.unavailable(self.at(moment).attempt(agent, case))

    def cool(self, agent: Agent, *models: ModelRef, until: datetime) -> None:
        """GIVEN 里的"这几个模型冷却到 until"：在 until 减冷却时间的时刻，在新的案件里让 agent 的
        这几个模型依次连续不可用到冷却。排在前面、不在冷却中的模型（只会是主模型）为了让起点到这个
        模型不可用一次，事后在新的案件里成功一次，回到正常。时钟最后拨回原来的时刻。
        """
        now = self.clock.now()
        self.clock.set(until - self.config.breaker_cooldown)
        for model in models:
            case = next(self._spare)
            step = self._attempt_in(case, agent)
            touched = []
            while step.call.model != model:
                touched.append(step.call.model)
                next_step = self.unavailable(step)
                assert isinstance(next_step, Attempt)
                step = next_step
            self.service.succeed(step.call)  # 本案件的起点是这个模型
            for _ in range(self.config.breaker_failures):
                self.unavailable(self._attempt_in(case, agent))
            assert self.service.cooling()[model] == until
            for earlier in touched:
                reset = self._attempt_in(next(self._spare), agent)
                assert reset.call.model == earlier
                self.service.succeed(reset.call)
        self.clock.set(now)

    def consecutive(self, agent: Agent) -> int:
        """agent 的主模型现在（正常时）连续不可用的次数：在新的案件里一次次报告它不可用，数到冷却
        为止。会改变熔断状态，只在测试最后用。
        """
        primary = self._attempt_in(next(self._spare), agent).call.model
        assert primary not in self.service.cooling()
        for extra in range(1, self.config.breaker_failures + 1):
            step = self._attempt_in(next(self._spare), agent)
            assert step.call.model == primary
            self.unavailable(step)
            if primary in self.service.cooling():
                return self.config.breaker_failures - extra
        raise AssertionError("报告了连续不可用次数那么多次，仍没有冷却")

    def _attempt_in(self, case: UUID, agent: Agent) -> Attempt:
        """在 case 里为 agent 开始一次没有截止时间的调用，第一步是一次尝试。"""
        step = self.service.begin(case, agent)
        assert isinstance(step, Attempt)
        return step


def _call(
    agent: Agent,
    chain: tuple[ModelRef, ...],
    index: int,
    attempt: int = 1,
    *gone: Unavailable,
    case: UUID = K_1,
    deadline: datetime | None = None,
) -> ModelCall:
    return ModelCall(case, agent, chain, index, attempt, deadline, gone)


PM_CHAIN, NEWS_CHAIN = (P1, P2, P3), (S1, S2)


# 什么算不可用


def test_retry_after_a_failure() -> None:
    """spec「失败以后重试」。"""
    setup = _Setup()
    first = setup.attempt()
    assert first.call.model == P1

    retry = setup.service.fail(first.call, RATE_LIMITED)

    assert retry == Attempt(_call(PM, PM_CHAIN, 0, 2), PM_TIMEOUT)
    use = setup.service.succeed(retry.call)
    assert use == _use(P1, False)
    assert setup.service.cooling() == {}
    assert setup.consecutive(PM) == 0


def test_unavailable_only_after_the_retries() -> None:
    """spec「重试用完才不可用」。"""
    setup = _Setup()
    retry = setup.service.fail(setup.attempt().call, TIMEOUT)
    assert isinstance(retry, Attempt)

    step = setup.service.fail(retry.call, CONNECTION)

    assert isinstance(step, Attempt)
    assert (step.call.model, step.call.attempt) == (P2, 1)
    assert step.call.unavailable == (Unavailable(P1, CONNECTION),)


@pytest.mark.parametrize("error", list(CallError))
def test_every_call_error_counts(error: CallError) -> None:
    """spec「每种调用错误都算」。"""
    setup = _Setup()

    step = setup.unavailable(setup.attempt(), error)

    assert isinstance(step, Attempt)
    assert step.call.model == P2
    assert step.call.unavailable == (Unavailable(P1, error),)


def test_invalid_format_is_not_unavailable() -> None:
    """spec「格式不合法不算不可用」：调用成功，实际使用的模型是 s-1，不切换；s-1 正常；T-1 的 Result
    是失败，原因是格式不合法，模型是 s-1（task-dispatch spec）。
    """
    setup = _Setup()
    tasks = task_service(setup.clock)
    (dispatch,) = tasks.dispatch(K1, Dispatcher.PM, [NEWS_REQUEST])
    step = setup.attempt(NEWS)
    assert (step.call.model, step.switches) == (S1, ())

    use = setup.service.succeed(step.call)
    result = tasks.receive(dispatch.task.task_id, "看多，理由见上", Decimal("0.02"), use)

    assert use == S1_USE
    assert (result.status, result.reason, result.model_use) == (
        ResultStatus.FAILED,
        TaskFailure.FORMAT_INVALID,
        S1_USE,
    )
    assert setup.service.cooling() == {}
    assert setup.consecutive(NEWS) == 0


# 尝试的超时


def test_timeouts_of_the_two_kinds_of_agents() -> None:
    """spec「两类 agent 的超时」。"""
    news = _Setup().attempt(NEWS)

    assert (news.call.model, news.timeout) == (S1, NEWS_TIMEOUT)
    for agent in (PM, CRITIC, REVIEWER):
        assert _Setup().attempt(agent).timeout == PM_TIMEOUT


def test_primary_and_first_fallback_fit_the_task_timeout() -> None:
    """spec「主模型和第一个备用模型放得进任务时限」。"""
    setup = _Setup()
    first = setup.attempt(NEWS)

    retry = setup.at(at(15, 1)).service.fail(first.call, TIMEOUT)
    assert isinstance(retry, Attempt)
    fallback = setup.at(at(15, 2)).service.fail(retry.call, TIMEOUT)

    assert isinstance(fallback, Attempt)
    assert (fallback.call.model, fallback.timeout) == (S2, NEWS_TIMEOUT)
    retry = setup.at(at(15, 3)).service.fail(fallback.call, TIMEOUT)
    assert isinstance(retry, Attempt)
    step = setup.at(at(15, 4)).service.fail(retry.call, TIMEOUT)
    assert isinstance(step, AllUnavailable)
    assert step.detail == "模型都不可用：google/s-1 超时；openai/s-2 超时"


@pytest.mark.parametrize(
    ("moment", "timeout"),
    [(at(15, 4, 30), timedelta(seconds=30)), (at(15, 4), timedelta(seconds=60))],
    ids=["15:04:30Z", "15:04:00Z"],
)
def test_timeout_within_the_time_left(moment: datetime, timeout: timedelta) -> None:
    """spec「超时不超过离截止时间还剩的时间」。"""
    step = _Setup().at(moment).attempt(NEWS)

    assert (step.call.model, step.timeout) == (S1, timeout)


def _retrying_at_15_04_10() -> tuple[_Setup, Attempt]:
    """spec「到了截止时间就停止」的 GIVEN：15:04:00Z 开始 News 为 T-1 的调用，s-1 在 15:04:10Z 失败
    （连接失败），重试 s-1。
    """
    setup = _Setup()
    first = setup.at(at(15, 4)).attempt(NEWS)
    retry = setup.at(at(15, 4, 10)).service.fail(first.call, CONNECTION)
    assert isinstance(retry, Attempt)
    assert (retry.call.model, retry.call.attempt) == (S1, 2)
    return setup, retry


def test_stop_at_the_deadline() -> None:
    """spec「到了截止时间就停止」：15:05:00Z s-1 又失败，这次调用停止，不换 s-2，不是全部不可用；
    s-1 记下一次不可用。15:04:59Z 又失败则换到 s-2，超时 1 秒。15:05:00Z 才开始则不尝试任何模型。
    """
    setup, retry = _retrying_at_15_04_10()
    stopped = setup.at(at(15, 5)).service.fail(retry.call, CONNECTION)

    assert stopped == DeadlineReached(retry.call)
    assert setup.consecutive(NEWS) == 1

    setup, retry = _retrying_at_15_04_10()
    switched = setup.at(at(15, 4, 59)).service.fail(retry.call, CONNECTION)

    assert isinstance(switched, Attempt)
    assert (switched.call.model, switched.timeout) == (S2, timedelta(seconds=1))

    late = _Setup().at(at(15, 5)).begin(NEWS)

    assert late == DeadlineReached(_call(NEWS, NEWS_CHAIN, 0, deadline=DEADLINE))


def test_no_deadline_for_pm_critic_reviewer() -> None:
    """spec「PM、Critic、Reviewer 没有截止时间」：16:00Z p-1 失败，重试 p-1，超时 180 秒。"""
    setup = _Setup()
    first = setup.attempt()

    step = setup.at(at(16)).service.fail(first.call, TIMEOUT)

    assert step == Attempt(_call(PM, PM_CHAIN, 0, 2), PM_TIMEOUT)


# 切换顺序


def test_switch_to_the_first_fallback() -> None:
    """spec「主模型不可用，换第一个备用模型」。"""
    assert _Setup().fallback_used() == _use(P2, True)


def test_switch_in_the_configured_order() -> None:
    """spec「按配置顺序逐个换」。"""
    setup = _Setup()
    first = setup.attempt()
    second = setup.unavailable(first)
    assert isinstance(second, Attempt)
    third = setup.unavailable(second, RATE_LIMITED)
    assert isinstance(third, Attempt)

    use = setup.service.succeed(third.call)

    assert [first.call.model, second.call.model, third.call.model] == [P1, P2, P3]
    assert use == _use(P3, True)


def test_all_unavailable() -> None:
    """spec「全部不可用」。"""
    setup = _Setup()
    step = setup.unavailable(setup.unavailable(setup.attempt()), RATE_LIMITED)

    final = setup.unavailable(step, CONNECTION)

    assert isinstance(final, AllUnavailable)
    assert (
        final.detail == "模型都不可用：anthropic/p-1 超时；anthropic/p-2 限流；google/p-3 连接失败"
    )


def test_no_fallback_models() -> None:
    """spec「没有备用模型」。"""
    setup = _Setup()

    step = setup.unavailable(setup.attempt(REVIEWER), CallError.SERVER_ERROR)

    assert isinstance(step, AllUnavailable)
    assert step.detail == "模型都不可用：anthropic/p-1 服务端出错"


def test_skip_a_cooling_model() -> None:
    """spec「跳过冷却中的模型」。"""
    setup = _Setup()
    setup.cool(PM, P2, until=at(15, 5))

    step = setup.unavailable(setup.attempt())

    assert isinstance(step, Attempt)
    assert step.call.model == P3
    final = setup.unavailable(step, RATE_LIMITED)
    assert isinstance(final, AllUnavailable)
    assert final.detail == "模型都不可用：anthropic/p-1 超时；anthropic/p-2 冷却中；google/p-3 限流"


def test_first_model_cooling() -> None:
    """spec「起点在冷却中」。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))

    step = setup.attempt()

    assert (step.call.model, step.call.attempt) == (P2, 1)


def test_every_model_cooling() -> None:
    """spec「全部在冷却中」。"""
    setup = _Setup()
    setup.cool(PM, P1, P2, P3, until=at(15, 5))

    step = setup.begin()

    assert isinstance(step, AllUnavailable)
    assert step.detail == (
        "模型都不可用：anthropic/p-1 冷却中；anthropic/p-2 冷却中；google/p-3 冷却中"
    )


# 案件内不切回


def test_no_switching_back_in_a_case() -> None:
    """spec「切到备用模型以后不切回」。"""
    setup = _Setup()
    setup.fallback_used()

    step = setup.attempt()

    assert setup.service.case_model(K_1, PM) == _use(P2, True)
    assert step.call.model == P2
    assert step.call.unavailable == ()


def test_start_only_moves_forward() -> None:
    """spec「起点不可用时只往后换」。"""
    setup = _Setup()
    setup.fallback_used()

    use = setup.succeed(setup.unavailable(setup.attempt()))

    assert use == _use(P3, True)
    assert setup.service.case_model(K_1, PM) == _use(P3, True)
    step = setup.unavailable(setup.attempt())
    assert isinstance(step, AllUnavailable)
    assert step.detail == "模型都不可用：google/p-3 超时"


def test_primary_success_keeps_the_start() -> None:
    """spec「用主模型成功不改变起点」。"""
    setup = _Setup()

    setup.succeed(setup.attempt())

    assert setup.service.case_model(K_1, PM) == _use(P1, False)


def test_all_unavailable_keeps_the_start() -> None:
    """spec「全部不可用不改变起点」。"""
    setup = _Setup()
    step = setup.unavailable(setup.unavailable(setup.unavailable(setup.attempt())))
    assert isinstance(step, AllUnavailable)

    again = setup.attempt()

    assert setup.service.case_model(K_1, PM) == _use(P1, False)
    assert again.call.model == P1


def test_parallel_calls_do_not_move_the_start_back() -> None:
    """spec「并行的调用不让起点往回移」。"""
    setup = _Setup()
    t1, t2 = setup.attempt(NEWS), setup.attempt(NEWS)
    assert (t1.call.model, t2.call.model) == (S1, S1)

    setup.succeed(setup.unavailable(t1))
    t2_use = setup.service.succeed(t2.call)

    assert setup.service.case_model(K_1, NEWS) == _use(S2, True)
    assert t2_use == _use(S1, False)


@pytest.mark.parametrize(("agent", "case"), [(QUANT, K_1), (NEWS, K_2)], ids=["Quant", "K-2"])
def test_start_per_case_and_agent(agent: Agent, case: UUID) -> None:
    """spec「按案件和 agent 分开」。"""
    setup = _Setup()
    setup.fallback_used(NEWS)

    assert setup.attempt(agent, case).call.model == S1


def test_case_end_clears_the_start() -> None:
    """spec「案件结束以后清掉」。"""
    setup = _Setup()
    setup.fallback_used(PM, K_1)
    setup.fallback_used(PM, K_2)

    setup.service.end_case(K_1)

    assert setup.service.case_model(K_1, PM) == _use(P1, False)
    assert setup.service.case_model(K_2, PM) == _use(P2, True)


# 熔断


def test_below_the_count_does_not_cool() -> None:
    """spec「不到 3 次不冷却」：p-1 正常，连续不可用 2 次；PM 在 K-2 调用时先试 p-1。"""
    setup = _Setup()
    setup.unavailable_at(PM, at(14, 58), at(15))

    assert setup.service.cooling() == {}
    assert setup.attempt(PM, K_2).call.model == P1
    assert setup.consecutive(PM) == 2


def test_three_in_a_row_cool() -> None:
    """spec「连续 3 次不可用就冷却」。"""
    setup = _Setup()

    setup.unavailable_at(PM, at(14, 52), at(14, 56), at(15))

    assert setup.service.cooling() == {P1: at(15, 5)}


def test_window_boundary() -> None:
    """spec「时间窗口的边界」。"""
    within = _Setup()
    within.unavailable_at(PM, at(14, 50), at(14, 55), at(15))

    assert within.service.cooling() == {P1: at(15, 5)}

    outside = _Setup()
    outside.unavailable_at(PM, at(14, 49, 59), at(14, 55), at(15))

    assert outside.service.cooling() == {}
    outside.unavailable_at(PM, at(15, 1))
    assert outside.service.cooling() == {P1: at(15, 6)}


def test_success_resets_the_count() -> None:
    """spec「成功让连续的不可用清零」：p-1 的成功；s-1 给出格式不合法的输出，也是成功。"""
    for agent in (PM, NEWS):
        setup = _Setup()
        setup.unavailable_at(agent, at(14, 56), at(14, 58))
        setup.at(at(14, 59)).succeed(setup.attempt(agent))
        setup.unavailable_at(agent, at(15))

        assert setup.service.cooling() == {}
        assert setup.consecutive(agent) == 1


def _cooling_with_a_call_started_before() -> tuple[_Setup, Attempt]:
    """p-1 在 14:52Z、14:56Z 不可用；14:59Z PM 在 K-2 调用、先试 p-1（冷却以前开始的调用）；p-1
    在 15:00Z 不可用，冷却到 15:05Z。
    """
    setup = _Setup()
    setup.unavailable_at(PM, at(14, 52), at(14, 56))
    before = setup.at(at(14, 59)).attempt(PM, K_2)
    setup.unavailable_at(PM, at(15))
    assert setup.service.cooling() == {P1: at(15, 5)}
    assert before.call.model == P1
    return setup, before


def test_unavailable_while_cooling_does_not_extend() -> None:
    """spec「冷却中再不可用不延长」。"""
    setup, before = _cooling_with_a_call_started_before()

    setup.at(at(15, 3)).unavailable(before)

    assert setup.service.cooling() == {P1: at(15, 5)}


def test_success_while_cooling_recovers() -> None:
    """spec「冷却中成功就恢复」：15:03Z p-1 在冷却以前就开始的调用里成功，p-1 正常；15:03Z PM 在
    K-2 调用时先试 p-1。
    """
    setup, before = _cooling_with_a_call_started_before()

    setup.at(at(15, 3)).succeed(before)

    assert setup.service.cooling() == {}
    assert setup.attempt(PM, K_2).call.model == P1


def test_cooling_end_boundary() -> None:
    """spec「冷却结束的边界」。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))

    assert setup.at(at(15, 4, 59)).attempt().call.model == P2
    assert setup.at(at(15, 5)).attempt().call.model == P1


def test_unavailable_after_cooling_cools_again() -> None:
    """spec「冷却结束以后又不可用就再冷却」。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))

    setup.unavailable_at(PM, at(15, 6))

    assert setup.service.cooling() == {P1: at(15, 11)}


def test_success_after_cooling_recovers() -> None:
    """spec「冷却结束以后成功就恢复正常」。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))

    setup.at(at(15, 6)).succeed(setup.attempt())
    setup.unavailable_at(PM, at(15, 7))

    assert setup.service.cooling() == {}
    assert setup.consecutive(PM) == 1


def test_agents_sharing_a_model() -> None:
    """spec「几个 agent 共用一个模型」。"""
    setup = _Setup()
    setup.unavailable_at(PM, at(14, 52), at(14, 56))
    setup.unavailable_at(REVIEWER, at(15))

    assert setup.service.cooling() == {P1: at(15, 5)}
    assert setup.at(at(15, 1)).attempt(PM, K_2).call.model == P2
    reviewer = setup.begin(REVIEWER, K_2)
    assert isinstance(reviewer, AllUnavailable)
    assert reviewer.detail == "模型都不可用：anthropic/p-1 冷却中"


def test_skipping_does_not_count() -> None:
    """spec「被跳过不计入熔断」。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))

    assert setup.at(at(15, 1)).attempt(PM, K_1).call.model == P2
    assert setup.at(at(15, 2)).attempt(PM, K_2).call.model == P2

    assert setup.service.cooling() == {P1: at(15, 5)}
    assert setup.at(at(15, 5)).attempt(PM, K_1).call.model == P1


def test_cooling_models() -> None:
    """spec「给出冷却中的模型」。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))
    setup.cool(CRITIC, C1, until=at(15, 8))

    assert setup.at(at(15)).service.cooling() == {P1: at(15, 5), C1: at(15, 8)}
    assert setup.at(at(15, 5)).service.cooling() == {C1: at(15, 8)}


def test_restart_starts_over() -> None:
    """spec「进程重启以后从头算」：新建网关表示进程重启。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))
    setup.succeed(setup.attempt())
    assert setup.service.case_model(K_1, PM) == _use(P2, True)

    setup.clock.set(at(15, 1))
    restarted = fallback_service(setup.clock)
    step = restarted.begin(K_1, PM)

    assert restarted.cooling() == {}
    assert isinstance(step, Attempt)
    assert step.call.model == P1


# 还有可用的模型


def test_all_normal_have_models() -> None:
    """spec「都正常」。"""
    service = _Setup().service

    assert service.has_available_model(PM)
    assert service.has_available_model(CRITIC)


def test_one_model_not_cooling_is_enough() -> None:
    """spec「还有一个不在冷却中就算有」。"""
    setup = _Setup()
    setup.cool(PM, P1, P2, until=at(15, 5))

    assert setup.service.has_available_model(PM)


def test_every_model_cooling_has_none() -> None:
    """spec「全部在冷却中」。"""
    setup = _Setup()
    setup.cool(PM, P1, P2, P3, until=at(15, 5))

    assert not setup.at(at(15)).service.has_available_model(PM)
    assert setup.service.has_available_model(CRITIC)
    assert setup.at(at(15, 5)).service.has_available_model(PM)

    critic = _Setup()
    critic.cool(CRITIC, C1, C2, until=at(15, 5))

    assert not critic.at(at(15)).service.has_available_model(CRITIC)


def test_unavailable_but_not_cooling_is_available() -> None:
    """spec「不可用但没有冷却仍算可用」。"""
    setup = _Setup()
    step = setup.unavailable(setup.unavailable(setup.unavailable(setup.attempt())))
    assert isinstance(step, AllUnavailable)

    assert setup.service.has_available_model(PM)


# 实际使用的模型


def test_primary_and_fallback_models() -> None:
    """spec「主模型和备用模型」。"""
    primary = _Setup()

    assert primary.succeed(primary.attempt()) == _use(P1, False)
    assert _Setup().fallback_used() == _use(P2, True)
    assert _Setup().fallback_used(NEWS) == _use(S2, True)


def test_fallback_is_per_agent() -> None:
    """spec「按 agent 判断是不是备用模型」。"""
    setup = _Setup()

    assert setup.succeed(setup.attempt(REVIEWER)) == _use(P1, False)


# 切换的事件


def test_switch_after_unavailable() -> None:
    """spec「不可用以后切换」。"""
    setup = _Setup()
    first = setup.attempt()

    step = setup.at(at(15, 2)).unavailable(first, RATE_LIMITED)

    assert isinstance(step, Attempt)
    assert step.switches == (ModelSwitch(K_1, PM, P1, P2, RATE_LIMITED, at(15, 2)),)


def test_skipping_is_a_switch() -> None:
    """spec「跳过冷却中的模型也是切换」。"""
    setup = _Setup()
    setup.cool(PM, P2, until=at(15, 5))
    first = setup.attempt()

    step = setup.at(at(15, 1)).unavailable(first, CONNECTION)

    assert isinstance(step, Attempt)
    assert step.switches == (
        ModelSwitch(K_1, PM, P1, P2, CONNECTION, at(15, 1)),
        ModelSwitch(K_1, PM, P2, P3, None, at(15, 1)),
    )


def test_skipping_at_the_start() -> None:
    """spec「一开始就跳过」。"""
    setup = _Setup()
    setup.cool(PM, P1, until=at(15, 5))

    step = setup.attempt()

    assert step.switches == (ModelSwitch(K_1, PM, P1, P2, None, at(15)),)


def test_no_switch_for_retries_or_all_unavailable() -> None:
    """spec「重试和全部不可用没有切换的事件」。"""
    setup = _Setup()
    retry = setup.service.fail(setup.attempt().call, TIMEOUT)
    assert isinstance(retry, Attempt)

    assert retry.switches == ()
    assert setup.service.succeed(retry.call) == _use(P1, False)

    reviewer = _Setup()
    step = reviewer.unavailable(reviewer.attempt(REVIEWER))
    assert isinstance(step, AllUnavailable)
    assert step.switches == ()


# 备用模型的限制


def test_blocked_conclusion_goes_back_to_the_pm() -> None:
    """spec「被限制以后退回 PM 重交」：p-2 提交的开仓方案被限制；接着 PM 在 K-1 调用，第一次尝试
    是 p-2（案件状态的部分见 tests/modules/scheduler/domain/test_stage.py）。
    """
    setup = _Setup()
    use = setup.fallback_used()

    assert blocked_decision(watch(decision=Decision.BUY), use, setup.config) is Decision.BUY
    assert setup.attempt().call.model == P2


# 备用模型的配置


def test_other_call_timeout() -> None:
    """spec「改成别的值」：PM、Critic、Reviewer 的单次调用超时配置为 300 秒。"""
    step = _Setup(call_timeout=timedelta(seconds=300)).attempt()

    assert step.timeout == timedelta(seconds=300)
