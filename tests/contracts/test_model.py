"""agent 和模型身份的测试：六个 agent、一个模型、实际使用的模型、一次尝试的 token 用量。

规则见 openspec 的 model-fallback spec「模型的配置」「实际使用的模型」和 call-budget spec「费用的
算法」，类型见 add-model-fallback 的 design D1 和 add-call-budget 的 design D2。
"""

from typing import Any

import pytest
from pydantic import ValidationError

from tradingagentspro.contracts.agent import Agent, SubAgent
from tradingagentspro.contracts.model import ModelRef, ModelUse, TokenUsage

P1 = ModelRef(vendor="anthropic", name="p-1")


# Agent


def test_six_agents() -> None:
    """六个 agent 各配一套模型：PM、News、Quant、Fundamentals、Critic、Reviewer。"""
    assert [agent.value for agent in Agent] == [
        "pm",
        "news",
        "quant",
        "fundamentals",
        "critic",
        "reviewer",
    ]


@pytest.mark.parametrize("sub_agent", list(SubAgent))
def test_sub_agents_have_the_same_values(sub_agent: SubAgent) -> None:
    """子 agent 的值和 SubAgent 相同：子 agent 的任务用 Agent(task.target) 转过来。"""
    assert Agent(sub_agent).value == sub_agent.value
    assert Agent(SubAgent.NEWS) is Agent.NEWS


# ModelRef


def test_vendor_is_stripped_and_lowercased() -> None:
    """spec「厂商去掉空白、转成小写」：厂商 " Anthropic " 是 anthropic，和 anthropic/p-1 是同一个
    模型。
    """
    model = ModelRef(vendor=" Anthropic ", name="p-1")

    assert model.vendor == "anthropic"
    assert model == P1


def test_name_is_stripped_and_keeps_its_case() -> None:
    """模型名去掉首尾空白，大小写不变：厂商的接口区分大小写。"""
    model = ModelRef(vendor="openai", name=" GPT-X ")

    assert model.name == "GPT-X"
    assert model != ModelRef(vendor="openai", name="gpt-x")


@pytest.mark.parametrize(
    "fields",
    [
        {"vendor": "  ", "name": "s-1"},
        {"vendor": "", "name": "s-1"},
        {"vendor": "openai", "name": ""},
        {"vendor": "openai", "name": "  "},
        {"name": "s-1"},
        {"vendor": "openai"},
    ],
    ids=[
        "厂商是空白",
        "厂商是空字符串",
        "模型名是空字符串",
        "模型名是空白",
        "没有厂商",
        "没有模型名",
    ],
)
def test_empty_vendor_or_name_is_an_error(fields: dict[str, Any]) -> None:
    """spec「配置不合法」：厂商是 "  "；模型名是 ""。都报错。"""
    with pytest.raises(ValidationError):
        ModelRef.model_validate(fields)


def test_same_vendor_and_name_is_the_same_model() -> None:
    """厂商和模型名都相同就是同一个模型：相等、哈希相同，可以做 dict 的键（熔断状态按模型存）。"""
    same = ModelRef(vendor="ANTHROPIC", name="p-1")
    breakers = {P1: "冷却中"}

    assert same == P1
    assert hash(same) == hash(P1)
    assert breakers[same] == "冷却中"
    assert ModelRef(vendor="anthropic", name="p-2") not in breakers
    assert ModelRef(vendor="google", name="p-1") not in breakers


def test_str_is_vendor_slash_name() -> None:
    """说明和日志里写作"厂商/模型名"。"""
    assert str(ModelRef(vendor=" Google ", name="s-1")) == "google/s-1"


# ModelUse


def test_model_use() -> None:
    """spec「实际使用的模型」：模型，以及它是不是这个 agent 的备用模型。"""
    use = ModelUse(model=P1, fallback=False)

    assert (use.model, use.fallback) == (P1, False)
    assert ModelUse.model_validate_json(use.model_dump_json()) == use


# TokenUsage


def test_token_usage() -> None:
    """输入和输出的 token 数；可以从 JSON 读回。"""
    usage = TokenUsage(input_tokens=20_000, output_tokens=2_000)

    assert (usage.input_tokens, usage.output_tokens) == (20_000, 2_000)
    assert TokenUsage.model_validate_json(usage.model_dump_json()) == usage


def test_zero_usage_is_valid() -> None:
    """call-budget spec「不舍入」：输入 0、输出 0 合法。"""
    usage = TokenUsage(input_tokens=0, output_tokens=0)

    assert (usage.input_tokens, usage.output_tokens) == (0, 0)


@pytest.mark.parametrize(
    "fields",
    [
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": 0, "output_tokens": -1},
        {"input_tokens": 0, "output_tokens": 1.5},
        {"input_tokens": 0},
        {"output_tokens": 0},
    ],
    ids=["输入 -1", "输出 -1", "输出 1.5", "没有输出", "没有输入"],
)
def test_invalid_usage_is_an_error(fields: dict[str, Any]) -> None:
    """call-budget spec「用量不合法」：用量小于 0 或者不是整数，都报错。"""
    with pytest.raises(ValidationError):
        TokenUsage.model_validate(fields)
