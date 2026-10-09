"""备用模型纯逻辑的测试：熔断、一次调用的步骤、尝试的超时、备用模型的限制，直接构造熔断状态、
ModelCall 和结论。

规则见 openspec 的 model-fallback spec「什么算不可用」「尝试的超时」「切换顺序」「熔断」「切换的
事件」「备用模型的限制」「备用模型的配置」；结论用 builders 里 case-types spec 的基准结论。

时刻都在 2026-10-14，写作 UTC；默认配置是重试 1 次，子 agent 每次尝试 60 秒、PM、Critic、Reviewer
180 秒，连续 3 次不可用、时间窗口 10 分钟、冷却 5 分钟。模型用 builders 里的基准模型的配置：PM 依次
是 p-1、p-2、p-3，News 是 s-1、s-2；News 的任务 T-1 截止 15:05Z。
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tests.modules.gateway.builders import (
    FOLLOW_UP,
    K1,
    NOW,
    P1,
    P2,
    P3,
    S1,
    S2,
    WATCH_THESIS,
    at,
    candidate,
    ddd,
    exploration,
    holding,
    watch,
)
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.conclusion import Conclusion, Decision
from tradingagentspro.contracts.model import ModelRef, ModelUse
from tradingagentspro.modules.gateway import (
    AllUnavailable,
    Attempt,
    Breaker,
    CallError,
    DeadlineReached,
    GatewayConfig,
    ModelCall,
    ModelSwitch,
    Unavailable,
    after_failure,
    attempt_timeout,
    blocked_decision,
    exhausted,
    start,
)

CONFIG = GatewayConfig()
CASE = K1.case_id
PM_CHAIN = (P1, P2, P3)
NEWS_CHAIN = (S1, S2)
DEADLINE = at(15, 5)  # T-1 的截止时间
NORMAL: Mapping[ModelRef, Breaker] = {}  # 所有模型都正常
TIMEOUT, CONNECTION, RATE_LIMITED = CallError.TIMEOUT, CallError.CONNECTION, CallError.RATE_LIMITED
PM_TIMEOUT, NEWS_TIMEOUT = timedelta(seconds=180), timedelta(seconds=60)


def _failed(*moments: datetime, config: GatewayConfig = CONFIG) -> Breaker:
    """从正常开始，依次在这些时刻不可用以后的状态。"""
    breaker = Breaker()
    for moment in moments:
        breaker = breaker.failed(moment, config)
    return breaker


COOLING = Breaker(cooling_until=at(15, 5))  # 冷却到 15:05Z


# 熔断：状态表的每一行


def test_normal_success_clears_the_failures() -> None:
    """正常 | 成功：正常，连续的不可用清零。"""
    assert _failed(at(14, 56), at(14, 58)).succeeded() == Breaker()


def test_normal_failure_below_the_count_is_recorded() -> None:
    """正常 | 不可用，算上这次不到 3 次：正常，记下这次。"""
    breaker = _failed(at(14, 58), at(15))

    assert breaker == Breaker(failures=(at(14, 58), at(15)))
    assert not breaker.cooling(at(15))


def test_normal_failure_outside_the_window_is_recorded() -> None:
    """正常 | 不可用，最近连续的 3 次里最早一次到这次超过时间窗口：正常，记下这次。"""
    breaker = _failed(at(14, 49, 59), at(14, 55), at(15))

    assert breaker == Breaker(failures=(at(14, 49, 59), at(14, 55), at(15)))
    assert not breaker.cooling(at(15))


def test_normal_failure_within_the_window_starts_cooling() -> None:
    """正常 | 不可用，最近连续的 3 次在时间窗口以内：冷却到这次加 5 分钟，连续的不可用清零。"""
    breaker = _failed(at(14, 52), at(14, 56), at(15))

    assert breaker == Breaker(cooling_until=at(15, 5))
    assert breaker.cooling(at(15))


def test_failure_while_cooling_keeps_the_end() -> None:
    """冷却中 | 不可用：冷却结束的时刻不变。"""
    assert COOLING.failed(at(15, 3), CONFIG) == COOLING


@pytest.mark.parametrize("moment", [at(15, 3), at(15, 6)], ids=["冷却中", "冷却结束"])
def test_success_while_cooling_or_cooled_returns_to_normal(moment: datetime) -> None:
    """冷却中 | 成功、冷却结束 | 成功：正常。"""
    breaker = COOLING.succeeded()

    assert breaker == Breaker()
    assert not breaker.cooling(moment)


def test_failure_after_cooling_cools_again() -> None:
    """冷却结束 | 不可用：冷却到这次加 5 分钟，不重新数满 3 次。"""
    breaker = COOLING.failed(at(15, 6), CONFIG)

    assert breaker == Breaker(cooling_until=at(15, 11))
    assert breaker.cooling(at(15, 10, 59))


# 熔断：边界


def test_one_below_the_count_does_not_cool() -> None:
    """连续不可用次数减 1 不冷却，等于时冷却。"""
    assert not _failed(at(14, 59), at(15)).cooling(at(15))
    assert _failed(at(14, 58), at(14, 59), at(15)).cooling(at(15))


@pytest.mark.parametrize(
    ("first", "cooling"),
    [(at(14, 50), True), (at(14, 49, 59), False)],
    ids=["正好 10 分钟", "多 1 秒"],
)
def test_window_boundary(first: datetime, cooling: bool) -> None:
    """spec「时间窗口的边界」：最早一次到这次等于时间窗口时冷却，多 1 秒不冷却。"""
    assert _failed(first, at(14, 55), at(15)).cooling(at(15)) is cooling


def test_window_slides_over_the_last_failures() -> None:
    """spec「时间窗口的边界」：14:49:59Z、14:55Z、15:00Z 不冷却，接着 15:01Z 冷却到 15:06Z，最近
    连续的 3 次是 14:55Z、15:00Z、15:01Z。
    """
    breaker = _failed(at(14, 49, 59), at(14, 55), at(15), at(15, 1))

    assert breaker == Breaker(cooling_until=at(15, 6))


def test_failures_keep_the_last_n() -> None:
    """只留最近"连续不可用次数"个时刻。"""
    breaker = _failed(at(14), at(14, 20), at(14, 40), at(15))

    assert breaker.failures == (at(14, 20), at(14, 40), at(15))


@pytest.mark.parametrize(
    ("moment", "cooling"),
    [(at(15, 4, 59), True), (at(15, 5), False), (at(15, 6), False)],
    ids=["前 1 秒", "等于", "之后"],
)
def test_cooling_end_boundary(moment: datetime, cooling: bool) -> None:
    """spec「冷却结束的边界」：冷却结束的时刻前 1 秒在冷却中，等于时不在。"""
    assert COOLING.cooling(moment) is cooling


def test_success_after_cooling_then_failure_counts_from_one() -> None:
    """spec「冷却结束以后成功就恢复正常」：15:06Z 成功，15:07Z 不可用，正常，连续不可用 1 次。"""
    breaker = COOLING.succeeded().failed(at(15, 7), CONFIG)

    assert breaker == Breaker(failures=(at(15, 7),))


def test_success_resets_the_count() -> None:
    """spec「成功让连续的不可用清零」：14:56Z、14:58Z 不可用，14:59Z 成功，15:00Z 不可用。"""
    breaker = _failed(at(14, 56), at(14, 58)).succeeded().failed(at(15), CONFIG)

    assert breaker == Breaker(failures=(at(15),))


# 备用模型的配置：改成别的值（熔断的三组）


def test_one_failure_cools_when_the_count_is_one() -> None:
    """熔断的连续不可用次数配置为 1：15:00Z 不可用，冷却到 15:05Z。"""
    config = GatewayConfig(breaker_failures=1)

    assert _failed(at(15), config=config) == Breaker(cooling_until=at(15, 5))


def test_shorter_window() -> None:
    """熔断的时间窗口配置为 5 分钟：14:52Z、14:56Z、15:00Z 不可用，正常。"""
    config = GatewayConfig(breaker_window=timedelta(minutes=5))

    assert not _failed(at(14, 52), at(14, 56), at(15), config=config).cooling(at(15))


def test_shorter_cooldown() -> None:
    """冷却时间配置为 1 分钟：14:52Z、14:56Z、15:00Z 不可用，冷却到 15:01Z。"""
    config = GatewayConfig(breaker_cooldown=timedelta(minutes=1))

    assert _failed(at(14, 52), at(14, 56), at(15), config=config) == Breaker(
        cooling_until=at(15, 1)
    )


# 一次调用的步骤


def _pm_call(index: int = 0, attempt: int = 1, *gone: Unavailable) -> ModelCall:
    """PM 在 K-1 的调用：正在向 PM_CHAIN[index] 做第 attempt 次尝试，gone 是已经不可用的模型。"""
    return ModelCall(CASE, Agent.PM, PM_CHAIN, index, attempt, None, gone)


def _news_call(index: int = 0, attempt: int = 1, *gone: Unavailable) -> ModelCall:
    """News 为 T-1 的调用，截止时间 15:05Z。"""
    return ModelCall(CASE, Agent.NEWS, NEWS_CHAIN, index, attempt, DEADLINE, gone)


def _cooling(*models: ModelRef) -> dict[ModelRef, Breaker]:
    """这些模型冷却到 15:05Z，其余正常。"""
    return {model: Breaker(cooling_until=at(15, 5)) for model in models}


def _switch(from_model: ModelRef, to_model: ModelRef, error: CallError | None) -> ModelSwitch:
    """PM 在 K-1 的调用里，现在从 from_model 换到 to_model。"""
    return ModelSwitch(CASE, Agent.PM, from_model, to_model, error, NOW)


def test_start_from_the_primary() -> None:
    """spec「两类 agent 的超时」：PM 在 K-1 调用，先试 p-1，超时 180 秒。"""
    step = start(CASE, Agent.PM, PM_CHAIN, 0, None, NORMAL, NOW, CONFIG)

    assert step == Attempt(_pm_call(), PM_TIMEOUT)
    assert step.call.model == P1


def test_start_from_the_case_model() -> None:
    """spec「切到备用模型以后不切回」：起点是 p-2 时第一次尝试是 p-2，不试 p-1。"""
    step = start(CASE, Agent.PM, PM_CHAIN, 1, None, NORMAL, NOW, CONFIG)

    assert step == Attempt(_pm_call(1), PM_TIMEOUT)


def test_start_skips_a_cooling_primary() -> None:
    """spec「起点在冷却中」「一开始就跳过」：跳过 p-1，第一次尝试是 p-2；切换的事件从 p-1 到 p-2，
    原因是冷却中，时刻 15:00Z。
    """
    step = start(CASE, Agent.PM, PM_CHAIN, 0, None, _cooling(P1), NOW, CONFIG)

    assert step == Attempt(
        _pm_call(1, 1, Unavailable(P1, None)), PM_TIMEOUT, (_switch(P1, P2, None),)
    )


def test_start_with_every_model_cooling() -> None:
    """spec「全部在冷却中」：不尝试任何模型，全部不可用，说明三个冷却中；越过 p-1 和 p-2 各是一次
    切换，p-3 后面没有模型，没有切换。
    """
    step = start(CASE, Agent.PM, PM_CHAIN, 0, None, _cooling(P1, P2, P3), NOW, CONFIG)

    assert step == AllUnavailable(
        CASE,
        Agent.PM,
        (Unavailable(P1, None), Unavailable(P2, None), Unavailable(P3, None)),
        (_switch(P1, P2, None), _switch(P2, P3, None)),
    )
    assert step.detail == (
        "模型都不可用：anthropic/p-1 冷却中；anthropic/p-2 冷却中；google/p-3 冷却中"
    )


@pytest.mark.parametrize("now", [at(15, 5), at(15, 6)], ids=["等于截止时间", "晚于截止时间"])
def test_start_at_the_deadline_stops(now: datetime) -> None:
    """spec「到了截止时间就停止」：15:05:00Z 才开始 News 为 T-1 的调用，停止，不尝试任何模型。"""
    step = start(CASE, Agent.NEWS, NEWS_CHAIN, 0, DEADLINE, _cooling(S1), now, CONFIG)

    assert step == DeadlineReached(_news_call())


def test_retry_the_same_model() -> None:
    """spec「失败以后重试」：p-1 失败（限流），重试 p-1，这是第 2 次尝试，没有切换。"""
    step = after_failure(_pm_call(), RATE_LIMITED, NORMAL, NOW, CONFIG)

    assert step == Attempt(_pm_call(0, 2), PM_TIMEOUT)


def test_retry_ignores_the_breaker() -> None:
    """重试不看熔断状态：p-1 在重试中途被别的调用熔断，这次照样重试。"""
    step = after_failure(_pm_call(), TIMEOUT, _cooling(P1), NOW, CONFIG)

    assert step == Attempt(_pm_call(0, 2), PM_TIMEOUT)


def test_no_retry_switches_after_the_first_failure() -> None:
    """重试次数为 0：第一次失败 p-1 就不可用，换到 p-2。"""
    config = GatewayConfig(call_retries=0)

    step = after_failure(_pm_call(), TIMEOUT, NORMAL, NOW, config)

    assert step == Attempt(
        _pm_call(1, 1, Unavailable(P1, TIMEOUT)), PM_TIMEOUT, (_switch(P1, P2, TIMEOUT),)
    )


def test_unavailable_after_the_retries() -> None:
    """spec「重试用完才不可用」「不可用以后切换」：第 2 次尝试失败（连接失败），p-1 不可用，原因是
    连接失败；换到 p-2，这是 p-2 的第 1 次尝试。
    """
    step = after_failure(_pm_call(0, 2), CONNECTION, NORMAL, NOW, CONFIG)

    assert step == Attempt(
        _pm_call(1, 1, Unavailable(P1, CONNECTION)), PM_TIMEOUT, (_switch(P1, P2, CONNECTION),)
    )


@pytest.mark.parametrize("error", list(CallError))
def test_every_call_error_counts(error: CallError) -> None:
    """spec「每种调用错误都算」：七种调用错误都一样处理，原因是这个调用错误。"""
    retried = after_failure(_pm_call(), error, NORMAL, NOW, CONFIG)
    step = after_failure(_pm_call(0, 2), error, NORMAL, NOW, CONFIG)

    assert retried == Attempt(_pm_call(0, 2), PM_TIMEOUT)
    assert step == Attempt(
        _pm_call(1, 1, Unavailable(P1, error)), PM_TIMEOUT, (_switch(P1, P2, error),)
    )


def test_skip_a_cooling_fallback() -> None:
    """spec「跳过冷却中的模型」「跳过冷却中的模型也是切换」：p-2 冷却中，p-1 不可用（连接失败）以后
    跳过 p-2、换到 p-3；依次两个切换的事件。
    """
    step = after_failure(_pm_call(0, 2), CONNECTION, _cooling(P2), NOW, CONFIG)

    assert step == Attempt(
        _pm_call(2, 1, Unavailable(P1, CONNECTION), Unavailable(P2, None)),
        PM_TIMEOUT,
        (_switch(P1, P2, CONNECTION), _switch(P2, P3, None)),
    )


def test_last_model_unavailable() -> None:
    """spec「全部不可用」：p-3 不可用（连接失败）以后全部不可用，没有切换的事件；说明按先后列出。"""
    call = _pm_call(2, 2, Unavailable(P1, TIMEOUT), Unavailable(P2, RATE_LIMITED))

    step = after_failure(call, CONNECTION, NORMAL, NOW, CONFIG)

    assert step == AllUnavailable(
        CASE,
        Agent.PM,
        (Unavailable(P1, TIMEOUT), Unavailable(P2, RATE_LIMITED), Unavailable(P3, CONNECTION)),
    )
    assert (
        step.detail == "模型都不可用：anthropic/p-1 超时；anthropic/p-2 限流；google/p-3 连接失败"
    )


def test_last_model_after_a_skip() -> None:
    """spec「跳过冷却中的模型」：p-2 冷却中，p-1 不可用、跳过 p-2、接着 p-3 不可用（限流），全部
    不可用。
    """
    call = _pm_call(2, 2, Unavailable(P1, TIMEOUT), Unavailable(P2, None))

    step = after_failure(call, RATE_LIMITED, _cooling(P2), NOW, CONFIG)

    assert isinstance(step, AllUnavailable)
    assert step.switches == ()
    assert step.detail == "模型都不可用：anthropic/p-1 超时；anthropic/p-2 冷却中；google/p-3 限流"


@pytest.mark.parametrize("attempt", [1, 2], ids=["还能重试", "重试用完"])
@pytest.mark.parametrize("now", [at(15, 5), at(15, 5, 1)], ids=["等于截止时间", "晚于截止时间"])
def test_failure_at_the_deadline_stops(attempt: int, now: datetime) -> None:
    """spec「到了截止时间就停止」：到了截止时间，不论还能不能重试、后面还有没有模型，都停止。"""
    call = _news_call(0, attempt)

    assert after_failure(call, CONNECTION, NORMAL, now, CONFIG) == DeadlineReached(call)


def test_failure_one_second_before_the_deadline_switches() -> None:
    """spec「到了截止时间就停止」：15:04:59Z s-1 又失败，s-1 不可用，换到 s-2，超时 1 秒。"""
    step = after_failure(_news_call(0, 2), TIMEOUT, NORMAL, at(15, 4, 59), CONFIG)

    assert step == Attempt(
        _news_call(1, 1, Unavailable(S1, TIMEOUT)),
        timedelta(seconds=1),
        (ModelSwitch(CASE, Agent.NEWS, S1, S2, TIMEOUT, at(15, 4, 59)),),
    )


# 尝试的超时


@pytest.mark.parametrize(
    ("now", "timeout"),
    [
        (at(15), timedelta(seconds=60)),
        (at(15, 4), timedelta(seconds=60)),
        (at(15, 4, 30), timedelta(seconds=30)),
        (at(15, 4, 59), timedelta(seconds=1)),
    ],
    ids=["15:00Z", "15:04:00Z", "15:04:30Z", "15:04:59Z"],
)
def test_sub_agent_timeout(now: datetime, timeout: timedelta) -> None:
    """spec「两类 agent 的超时」「超时不超过离截止时间还剩的时间」：60 秒和离截止时间还剩的时间
    里较短的。
    """
    assert attempt_timeout(_news_call(), now, CONFIG) == timeout


@pytest.mark.parametrize("agent", [Agent.QUANT, Agent.FUNDAMENTALS])
def test_other_sub_agents_use_the_sub_agent_timeout(agent: Agent) -> None:
    call = replace(_news_call(), agent=agent)

    assert attempt_timeout(call, at(15, 4, 30), CONFIG) == timedelta(seconds=30)


@pytest.mark.parametrize("agent", [Agent.PM, Agent.CRITIC, Agent.REVIEWER])
def test_pm_critic_reviewer_timeout(agent: Agent) -> None:
    """spec「两类 agent 的超时」：PM、Critic、Reviewer 都是 180 秒，不看截止时间。"""
    call = replace(_pm_call(), agent=agent)

    assert attempt_timeout(call, NOW, CONFIG) == PM_TIMEOUT
    assert attempt_timeout(replace(call, deadline=at(15, 1)), NOW, CONFIG) == PM_TIMEOUT


def test_other_call_timeout() -> None:
    """spec「改成别的值」：PM、Critic、Reviewer 的单次调用超时配置为 300 秒。"""
    config = GatewayConfig(call_timeout=timedelta(seconds=300))

    assert attempt_timeout(_pm_call(), NOW, config) == timedelta(seconds=300)


# 重试用完


@pytest.mark.parametrize(
    ("retries", "attempt", "expected"),
    [(1, 1, False), (1, 2, True), (0, 1, True), (2, 2, False), (2, 3, True)],
    ids=[
        "重试 1 次的第 1 次",
        "重试 1 次的第 2 次",
        "不重试",
        "重试 2 次的第 2 次",
        "重试 2 次的第 3 次",
    ],
)
def test_exhausted(retries: int, attempt: int, expected: bool) -> None:
    """这次尝试是这个模型在这次调用里的最后一次：尝试次数大于重试次数。"""
    config = GatewayConfig(call_retries=retries, task_timeout=timedelta(minutes=10))

    assert exhausted(_pm_call(0, attempt), config) is expected


# 全部不可用的说明


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (CallError.TIMEOUT, "超时"),
        (CallError.CONNECTION, "连接失败"),
        (CallError.RATE_LIMITED, "限流"),
        (CallError.QUOTA_EXHAUSTED, "额度或余额用完"),
        (CallError.AUTH_FAILED, "认证失败"),
        (CallError.SERVER_ERROR, "服务端出错"),
        (CallError.OTHER, "其他调用错误"),
        (None, "冷却中"),
    ],
)
def test_detail_names_each_reason(error: CallError | None, text: str) -> None:
    """说明里每个模型的原因：调用错误按 spec「什么算不可用」的表，被跳过的是冷却中。"""
    step = AllUnavailable(CASE, Agent.REVIEWER, (Unavailable(P1, error),))

    assert step.detail == f"模型都不可用：anthropic/p-1 {text}"


# 备用模型的限制

P1_USE = ModelUse(model=P1, fallback=False)  # 提交结论的调用用的是 p-1
P2_USE = ModelUse(model=P2, fallback=True)
P3_USE = ModelUse(model=P3, fallback=True)
BUY, ADD, PROMOTE = Decision.BUY, Decision.ADD, Decision.PROMOTE


@pytest.mark.parametrize(
    ("conclusion", "blocked"),
    [
        (watch(decision=BUY), BUY),
        (holding(decision=ADD), ADD),
        (candidate(decision=PROMOTE), PROMOTE),
    ],
    ids=["关注：开仓方案", "持仓：加仓", "候选：升级"],
)
def test_blocked_with_a_fallback_model(conclusion: Conclusion, blocked: Decision) -> None:
    """spec「用备用模型时被限制的决定」：提交结论的调用用的是 p-2，都被限制，给出这个决定。"""
    assert blocked_decision(conclusion, P2_USE, CONFIG) is blocked


@pytest.mark.parametrize(
    "conclusion",
    [
        holding(),
        holding(decision=Decision.REDUCE),
        holding(decision=Decision.CLOSE),
        watch(watch_thesis=WATCH_THESIS, follow_up=FOLLOW_UP),
        watch(decision=Decision.DEMOTE),
        watch(decision=Decision.REMOVE),
        candidate(),
        candidate(decision=Decision.REMOVE),
        exploration(ddd()),
    ],
    ids=[
        "持仓：持有",
        "持仓：减仓",
        "持仓：清仓",
        "关注：继续关注（附论点更新和跟进申请）",
        "关注：降为候选",
        "关注：移出",
        "候选：保持",
        "候选：移出",
        "寻股：加入 DDD",
    ],
)
def test_other_decisions_pass_with_a_fallback_model(conclusion: Conclusion) -> None:
    """spec「用备用模型时其他决定都通过」：提交结论的调用用的是 p-3，都通过。"""
    assert blocked_decision(conclusion, P3_USE, CONFIG) is None


@pytest.mark.parametrize(
    "conclusion", [watch(decision=BUY), holding(decision=ADD)], ids=["开仓方案", "加仓"]
)
def test_primary_model_is_not_limited(conclusion: Conclusion) -> None:
    """spec「用主模型时不限制」：提交结论的调用用的是 p-1，都通过。"""
    assert blocked_decision(conclusion, P1_USE, CONFIG) is None


def test_no_limit_configured() -> None:
    """spec「不限制或者只限制一部分」：不能提的决定配置为空，用 p-2 提交开仓方案，通过。"""
    config = GatewayConfig(fallback_blocked_decisions=frozenset())

    assert blocked_decision(watch(decision=BUY), P2_USE, config) is None


def test_only_buy_limited() -> None:
    """spec「不限制或者只限制一部分」：不能提的决定配置为只有开仓方案，用 p-2 提交加仓，通过；开仓
    方案仍被限制。
    """
    config = GatewayConfig(fallback_blocked_decisions=frozenset({BUY}))

    assert blocked_decision(holding(decision=ADD), P2_USE, config) is None
    assert blocked_decision(watch(decision=BUY), P2_USE, config) is BUY
