"""Agent 网关配置的测试。场景见 openspec 的 signal-format spec「信号的配置」、task-dispatch spec
「派发的配置」、model-fallback spec「模型的配置」「厂商校验」「备用模型的配置」，以及 call-budget
spec「模型的单价」「预算的配置」。

模型用 builders 里的基准模型的配置和基准单价，模型写作"厂商/模型名"。
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tests.modules.gateway.builders import (
    BASELINE_MODELS,
    BASELINE_PRICES,
    C1,
    C2,
    P1,
    P2,
    P3,
    S1,
    S2,
    assignments,
    price,
)
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.conclusion import Decision
from tradingagentspro.contracts.model import ModelRef
from tradingagentspro.modules.gateway import AgentModels, GatewayConfig, ModelPrice


def test_default_config() -> None:
    """signal-format spec「默认配置」：评估窗口的选项是 3、5、20、60。"""
    assert GatewayConfig().signal_horizons == (3, 5, 20, 60)


def test_one_horizon_is_valid() -> None:
    assert GatewayConfig(signal_horizons=(5,)).signal_horizons == (5,)


@pytest.mark.parametrize("horizons", [(), (0, 5)], ids=["空", "0 和 5"])
def test_invalid_config_is_an_error(horizons: tuple[int, ...]) -> None:
    """signal-format spec「配置不合法」：评估窗口的选项为空，或者是 0 和 5。"""
    with pytest.raises(ValidationError):
        GatewayConfig(signal_horizons=horizons)


def test_default_dispatch_config() -> None:
    """task-dispatch spec「默认配置」：PM 的任务数上限 6，PM 的轮次上限 2，Critic 的补充研究
    额度 2，任务时限 5 分钟。
    """
    config = GatewayConfig()

    assert config.max_pm_tasks == 6
    assert config.max_pm_rounds == 2
    assert config.max_critic_tasks == 2
    assert config.task_timeout == timedelta(minutes=5)


def test_smallest_valid_dispatch_config() -> None:
    """四项都取最小的合法值：1 个、1 轮、1 个、1 秒。重试次数 0、子 agent 的单次调用超时 0.5 秒，
    主模型和第一个备用模型的尝试才放得进 1 秒的任务时限。
    """
    config = GatewayConfig(
        max_pm_tasks=1,
        max_pm_rounds=1,
        max_critic_tasks=1,
        task_timeout=timedelta(seconds=1),
        call_retries=0,
        subagent_call_timeout=timedelta(seconds=0.5),
    )

    assert (config.max_pm_tasks, config.max_pm_rounds, config.max_critic_tasks) == (1, 1, 1)
    assert config.task_timeout == timedelta(seconds=1)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_pm_tasks": 0},
        {"max_pm_rounds": 0},
        {"max_critic_tasks": 0},
        {"task_timeout": timedelta(0)},
    ],
    ids=["PM 的任务数上限 0", "PM 的轮次上限 0", "Critic 的补充研究额度 0", "任务时限 0 秒"],
)
def test_invalid_dispatch_config_is_an_error(changes: dict[str, Any]) -> None:
    """task-dispatch spec「配置不合法」：都配置报错，错误指出这一项必须大于 0。"""
    with pytest.raises(ValidationError) as raised:
        GatewayConfig.model_validate(changes)

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [
        ("greater_than", tuple(changes))
    ]


# 模型的配置


def test_baseline_models() -> None:
    """spec「基准配置」：PM 的模型依次是 p-1、p-2、p-3；Reviewer 只有主模型 p-1，和 PM 的主模型
    是同一个模型。
    """
    pm, reviewer = BASELINE_MODELS.of(Agent.PM), BASELINE_MODELS.of(Agent.REVIEWER)

    assert set(BASELINE_MODELS.agents) == set(Agent)
    assert pm.chain == (P1, P2, P3)
    assert reviewer == AgentModels(primary=P1)
    assert reviewer.chain == (P1,)
    assert reviewer.primary == pm.primary
    assert BASELINE_MODELS.of(Agent.NEWS).chain == (S1, S2)
    assert BASELINE_MODELS.of(Agent.CRITIC).chain == (C1, C2)


def test_vendor_is_stripped_and_lowercased() -> None:
    """spec「厂商去掉空白、转成小写」：PM 的主模型的厂商写成 " Anthropic "。"""
    models = assignments(pm={"primary": {"vendor": " Anthropic ", "name": "p-1"}})

    assert models.of(Agent.PM).primary.vendor == "anthropic"
    assert models.of(Agent.PM).primary == models.of(Agent.REVIEWER).primary


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewer": None},
        {"news": {"primary": {"vendor": "  ", "name": "s-1"}}},
        {"critic": {"primary": C1, "fallbacks": ({"vendor": "openai", "name": ""},)}},
        {"pm": {"primary": P1, "fallbacks": (P2, P1)}},
        {"pm": {"primary": P1, "fallbacks": (P2, P2)}},
        {"pm": {"primary": P1, "fallbacks": ({"vendor": "Anthropic", "name": "p-1"},)}},
    ],
    ids=[
        "不配 Reviewer",
        "News 的主模型的厂商是空白",
        "Critic 的备用模型的模型名是空字符串",
        "PM 的备用模型依次是 p-2、p-1",
        "PM 的备用模型依次是 p-2、p-2",
        "PM 的备用模型是 Anthropic/p-1",
    ],
)
def test_invalid_models(changes: dict[str, Any]) -> None:
    """spec「配置不合法」：都配置报错。"""
    with pytest.raises(ValidationError):
        assignments(**changes)


def test_missing_agents_are_named() -> None:
    """缺少 agent 时，错误指出缺少哪几个。"""
    with pytest.raises(ValidationError, match="缺少这些 agent 的模型：critic、reviewer"):
        assignments(critic=None, reviewer=None)


def test_different_agents_may_share_a_model() -> None:
    """不同 agent 可以用同一个模型：三个子 agent 都用 s-1、s-2，Reviewer 和 PM 都用 p-1。"""
    assert BASELINE_MODELS.of(Agent.QUANT) == BASELINE_MODELS.of(Agent.NEWS)


# 厂商校验


def test_baseline_vendors_pass() -> None:
    """spec「基准配置通过」：PM 是 anthropic 和 google，Critic 是 openai；News 的备用模型 s-2 也是
    openai，Reviewer 也是 anthropic。
    """
    assert {m.vendor for m in BASELINE_MODELS.of(Agent.PM).chain} == {"anthropic", "google"}
    assert {m.vendor for m in BASELINE_MODELS.of(Agent.CRITIC).chain} == {"openai"}
    assert BASELINE_MODELS.of(Agent.NEWS).fallbacks[0].vendor == "openai"
    assert BASELINE_MODELS.of(Agent.REVIEWER).primary.vendor == "anthropic"


@pytest.mark.parametrize(
    "changes",
    [
        {"critic": {"primary": C1, "fallbacks": (ModelRef(vendor="google", name="c-2"),)}},
        {"pm": {"primary": P1, "fallbacks": (P2, ModelRef(vendor="openai", name="p-3"))}},
        {"critic": {"primary": {"vendor": "Anthropic", "name": "c-1"}, "fallbacks": (C2,)}},
    ],
    ids=[
        "Critic 的备用模型是 google/c-2",
        "PM 的 p-3 是 openai/p-3",
        "Critic 的主模型是 Anthropic/c-1",
    ],
)
def test_critic_and_pm_share_a_vendor(changes: dict[str, Any]) -> None:
    """spec「Critic 和 PM 有相同的厂商」：都配置报错，错误指出相同的厂商。"""
    with pytest.raises(ValidationError, match="Critic 和 PM 不能用同一家厂商的模型"):
        assignments(**changes)


# 备用模型的配置


def test_default_fallback_config() -> None:
    """spec「默认配置」。"""
    config = GatewayConfig()

    assert config.call_retries == 1
    assert config.subagent_call_timeout == timedelta(seconds=60)
    assert config.call_timeout == timedelta(seconds=180)
    assert config.breaker_failures == 3
    assert config.breaker_window == timedelta(minutes=10)
    assert config.breaker_cooldown == timedelta(minutes=5)
    assert config.fallback_blocked_decisions == {Decision.BUY, Decision.ADD, Decision.PROMOTE}


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"call_retries": -1}, "greater_than_equal"),
        ({"subagent_call_timeout": timedelta(0)}, "greater_than"),
        ({"call_timeout": timedelta(0)}, "greater_than"),
        ({"breaker_failures": 0}, "greater_than"),
        ({"breaker_window": timedelta(0)}, "greater_than"),
        ({"breaker_cooldown": timedelta(0)}, "greater_than"),
    ],
    ids=[
        "重试次数 -1",
        "子 agent 的单次调用超时 0 秒",
        "PM、Critic、Reviewer 的单次调用超时 0 秒",
        "熔断的连续不可用次数 0",
        "熔断的时间窗口 0 秒",
        "冷却时间 0 秒",
    ],
)
def test_invalid_fallback_config(changes: dict[str, Any], error: str) -> None:
    """spec「配置不合法」（备用模型的配置）：都配置报错，错误指出这一项。"""
    with pytest.raises(ValidationError) as raised:
        GatewayConfig.model_validate(changes)

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [(error, tuple(changes))]


def test_no_blocked_decisions_is_valid() -> None:
    """PM 用备用模型时不能提的决定可以为空。"""
    assert GatewayConfig(fallback_blocked_decisions=frozenset()).fallback_blocked_decisions == set()


@pytest.mark.parametrize(
    "changes",
    [
        {"subagent_call_timeout": timedelta(seconds=75)},
        {"call_retries": 0, "subagent_call_timeout": timedelta(seconds=150)},
        {"call_retries": 2, "task_timeout": timedelta(minutes=6)},
    ],
    ids=["单次超时 75 秒", "重试 0 次、单次超时 150 秒", "重试 2 次、任务时限 6 分钟"],
)
def test_attempts_fit_the_task_timeout(changes: dict[str, Any]) -> None:
    """spec「放得进任务时限的边界」：重试次数加 1，乘单次超时，再乘 2，等于任务时限，配置正确。"""
    config = GatewayConfig.model_validate(changes)

    assert (config.call_retries + 1) * config.subagent_call_timeout * 2 == config.task_timeout


@pytest.mark.parametrize(
    "changes",
    [{"subagent_call_timeout": timedelta(seconds=76)}, {"call_retries": 2}],
    ids=["单次超时 76 秒", "重试 2 次"],
)
def test_attempts_do_not_fit_the_task_timeout(changes: dict[str, Any]) -> None:
    """spec「放得进任务时限的边界」：超过任务时限，配置报错。"""
    with pytest.raises(ValidationError, match="放不进任务时限"):
        GatewayConfig.model_validate(changes)


# 模型的单价（call-budget spec）

X9 = ModelRef(vendor="openai", name="x-9")  # 没有 agent 用到的模型


def _prices(target: ModelRef, /, **fields: Any) -> list[ModelPrice | dict[str, Any]]:
    """基准单价，只把 target 的单价的这几项改为 fields（可以是不合法的值）。"""
    return [p.model_dump() | fields if p.model == target else p for p in BASELINE_PRICES]


def test_baseline_prices() -> None:
    """call-budget spec「基准单价」：配置正确；p-1 的输入单价 2、输出单价 10。"""
    p1 = BASELINE_MODELS.price(P1)

    assert (p1.input_price, p1.output_price) == (Decimal(2), Decimal(10))
    assert BASELINE_MODELS.price(P3) == price(P3, "1", "4")
    assert BASELINE_MODELS.price(C2) == price(C2, "1.25", "10")
    assert BASELINE_MODELS.price(S1) == price(S1, "0.3", "2.5")


def test_price_of_an_unconfigured_model() -> None:
    """取不在配置里的模型的单价抛 KeyError：调用方只用配置里的模型。"""
    with pytest.raises(KeyError):
        BASELINE_MODELS.price(X9)


def test_price_matches_the_normalized_vendor() -> None:
    """单价的模型和 agent 的模型一样去掉空白、转成小写：按同一个模型取单价。"""
    models = assignments(prices=_prices(P1, model={"vendor": " Anthropic ", "name": "p-1"}))

    assert models.price(P1) == price(P1, "2", "10")


def test_missing_price_names_the_models() -> None:
    """call-budget spec「缺少单价或单价不合法」：不配 c-2 的单价，配置报错，错误指出没有单价的
    模型。
    """
    prices = [p for p in BASELINE_PRICES if p.model != C2]

    with pytest.raises(ValidationError, match="这些模型没有单价：openai/c-2"):
        assignments(prices=prices)


def test_missing_prices_of_several_models() -> None:
    """几个模型没有单价时都列出来：只配 PM 的三个模型的单价。"""
    prices = [p for p in BASELINE_PRICES if p.model in (P1, P2, P3)]

    with pytest.raises(
        ValidationError, match="这些模型没有单价：google/s-1、openai/c-1、openai/c-2、openai/s-2"
    ):
        assignments(prices=prices)


@pytest.mark.parametrize(
    ("prices", "loc"),
    [
        (_prices(P3, input_price="-0.01"), ("prices", 2, "input_price")),
        (_prices(S1, output_price="-1"), ("prices", 5, "output_price")),
    ],
    ids=["p-3 的输入单价 -0.01", "s-1 的输出单价 -1"],
)
def test_negative_price_is_an_error(prices: list[Any], loc: tuple[Any, ...]) -> None:
    """call-budget spec「缺少单价或单价不合法」：单价小于 0，配置报错，错误指出这一项。"""
    with pytest.raises(ValidationError) as raised:
        assignments(prices=prices)

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [("greater_than_equal", loc)]


def test_two_prices_for_one_model() -> None:
    """call-budget spec「缺少单价或单价不合法」：p-1 配了两份单价，配置报错。"""
    prices = (*BASELINE_PRICES, price(P1, "3", "15"))

    with pytest.raises(ValidationError, match="同一个模型不能配两份单价"):
        assignments(prices=prices)


def test_zero_and_extra_prices_are_valid() -> None:
    """call-budget spec「单价可以是 0，可以多配」：s-2 的两个单价都是 0；另外给没有 agent 用到的
    openai/x-9 配了单价。都配置正确。
    """
    free = assignments(prices=_prices(S2, input_price="0", output_price="0"))
    extra = assignments(prices=(*BASELINE_PRICES, price(X9, "5", "20")))

    assert free.price(S2) == price(S2, "0", "0")
    assert extra.price(X9) == price(X9, "5", "20")


# 预算的配置（call-budget spec）


def test_default_budget_config() -> None:
    """call-budget spec「默认配置」：普通预算的额度 2.00，持仓保留预算的额度 1.00，寻股预算的额度
    0.50。
    """
    config = GatewayConfig()

    assert config.general_budget == Decimal("2.00")
    assert config.reserve_budget == Decimal("1.00")
    assert config.exploration_budget == Decimal("0.50")


@pytest.mark.parametrize(
    "changes",
    [
        {"general_budget": Decimal(0)},
        {"reserve_budget": Decimal(-1)},
        {"exploration_budget": Decimal(0)},
    ],
    ids=["普通预算的额度 0", "持仓保留预算的额度 -1", "寻股预算的额度 0"],
)
def test_invalid_budget_config(changes: dict[str, Any]) -> None:
    """call-budget spec「配置不合法」（预算的配置）：都配置报错，错误指出这一项必须大于 0。"""
    with pytest.raises(ValidationError) as raised:
        GatewayConfig.model_validate(changes)

    assert [(e["type"], e["loc"]) for e in raised.value.errors()] == [
        ("greater_than", tuple(changes))
    ]
