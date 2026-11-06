"""模型文件：厂商的接口和 key、六个 agent 的模型、模型的单价和三个预算的额度，见 model-config spec、
add-model-config 的 design D2 到 D5 和 add-model-adapter 的 design D2、D10、D11。

这里只接住文件的结构，写错的项在这里报错；范围和业务校验由 GatewayConfig 和 ModelAssignments 做。
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from tradingagentspro.bootstrap.files import ensure_private_file
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.model import ModelRef
from tradingagentspro.integrations.llm import Endpoint, Interface
from tradingagentspro.modules.gateway import (
    AgentModels,
    GatewayConfig,
    ModelAssignments,
    ModelPrice,
)

MODELS_FILE = Path("config/models.toml")  # 相对工作目录，不进 git
MODELS_TEMPLATE = Path(__file__).with_name("models.template.toml")


class RealVendor(BaseModel):
    """一家真实的厂商：接口、地址和 key。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interface: Interface
    base_url: str = Field(min_length=1)
    api_key: SecretStr = Field(min_length=1)


class StandInVendor(BaseModel):
    """替身的厂商：没有地址和 key。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interface: Literal["standin"]


class _Budgets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    general: Decimal
    reserve: Decimal
    exploration: Decimal


class _AgentEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: str
    fallbacks: tuple[str, ...] = ()


class _Price(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input: Decimal
    output: Decimal


class _ModelFile(BaseModel):
    # 输入里有 key，不能带进错误信息和启动失败的日志
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    budgets: _Budgets
    vendors: Mapping[str, RealVendor | StandInVendor]
    agents: Mapping[Agent, _AgentEntry]
    prices: Mapping[str, _Price]


@dataclass(frozen=True, slots=True)
class ModelFile:
    """读到的模型文件：网关的模型配置、网关配置（三个额度来自这个文件）和厂商。"""

    models: ModelAssignments
    gateway: GatewayConfig
    vendors: Mapping[str, RealVendor | StandInVendor]


def load_models(path: Path, gateway: GatewayConfig) -> ModelFile:
    """读模型文件，三个额度合进 gateway；出错时抛异常，由启动的这一步转成启动失败。"""
    text = path.read_text(encoding="utf-8")
    file = _ModelFile.model_validate(tomllib.loads(text, parse_float=Decimal))
    models = ModelAssignments(
        agents={
            agent: AgentModels(
                primary=_model(entry.primary),
                fallbacks=tuple(_model(model) for model in entry.fallbacks),
            )
            for agent, entry in file.agents.items()
        },
        prices=tuple(
            ModelPrice(model=_model(model), input_price=price.input, output_price=price.output)
            for model, price in file.prices.items()
        ),
    )
    budgets = {
        "general_budget": file.budgets.general,
        "reserve_budget": file.budgets.reserve,
        "exploration_budget": file.budgets.exploration,
    }
    undefined = sorted(used_vendors(models) - set(file.vendors))
    if undefined:
        raise ValueError(f"这些厂商没有写在 vendors 里：{'、'.join(undefined)}")
    return ModelFile(
        models, GatewayConfig.model_validate(gateway.model_dump() | budgets), file.vendors
    )


def used_vendors(models: ModelAssignments) -> set[str]:
    """agent 的主模型和备用模型用到的厂商。"""
    return {model.vendor for agent in Agent for model in models.of(agent).chain}


def _model(text: str) -> ModelRef:
    """模型写作"厂商/模型名"：第一个 / 前面是厂商，后面是模型名；缺的部分由 ModelRef 报错。"""
    vendor, _, name = text.partition("/")
    return ModelRef(vendor=vendor, name=name)


def endpoints(file: ModelFile) -> dict[str, Endpoint]:
    """agent 用到的真实厂商的连接。"""
    return {
        vendor: Endpoint(interface=entry.interface, base_url=entry.base_url, api_key=entry.api_key)
        for vendor in used_vendors(file.models)
        if isinstance(entry := file.vendors[vendor], RealVendor)
    }


def ensure_models_file(path: Path) -> bool:
    """模型文件不存在时从模板生成，只有所有者能读写；给出这次有没有生成。"""
    return ensure_private_file(path, MODELS_TEMPLATE.read_text(encoding="utf-8"))
