"""替身的配置和替身的模型配置，见 stand-ins spec「替身的配置」「替身的模型」和 add-stand-ins 的
design D7、D11。
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.model import ModelRef
from tradingagentspro.modules.gateway import AgentModels, ModelAssignments, ModelPrice

_VENDOR = "standin"
_CRITIC_VENDOR = "standin-critic"  # 和 PM 不同厂商，满足厂商校验


class StandInConfig(BaseModel):
    """替身的配置和默认值，含义见 stand-ins spec「替身的配置」。不和别的模块的配置互相检查。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 替身的信号（PM 的和子 agent 的）的评估窗口、评分和置信度
    signal_horizon: PositiveInt = 5
    signal_score: int = Field(default=1, ge=-2, le=2)
    signal_confidence: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    # 开仓方案：股数是权益乘比例再除以限价；限价比参考价高出、止损价比限价低的比例；紧急阈值
    position_ratio: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    limit_markup: Decimal = Field(default=Decimal("0.005"), ge=0, lt=1)
    stop_ratio: Decimal = Field(default=Decimal("0.05"), gt=0, lt=1)
    emergency_threshold: Decimal = Field(default=Decimal("0.08"), gt=0, lt=1)
    # 投资论点的目标价比限价高出的比例；升级的入场条件比参考价高出的比例
    target_ratio: Decimal = Field(default=Decimal("0.10"), gt=0)
    entry_markup: Decimal = Field(default=Decimal("0.02"), ge=0)
    # 新设和续期的复查日、新的催化剂的截止日期是今天之后第几个交易日
    review_sessions: PositiveInt = 5
    catalyst_sessions: PositiveInt = 8
    # 寻股加入粗筛名单的前几只
    exploration_additions: PositiveInt = 2


def stand_in_models() -> ModelAssignments:
    """替身的模型配置：每个 agent 一个模型，没有备用模型，单价 0。"""
    models = {agent: ModelRef(vendor=_VENDOR, name=agent.value) for agent in Agent}
    models[Agent.CRITIC] = ModelRef(vendor=_CRITIC_VENDOR, name=Agent.CRITIC.value)
    return ModelAssignments(
        agents={agent: AgentModels(primary=model) for agent, model in models.items()},
        prices=tuple(
            ModelPrice(model=model, input_price=Decimal(0), output_price=Decimal(0))
            for model in models.values()
        ),
    )
