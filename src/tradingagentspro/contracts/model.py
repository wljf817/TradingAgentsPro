"""模型的身份和实际使用的模型，见 model-fallback spec「模型的配置」「实际使用的模型」。

各 agent 用哪些模型由 Agent 网关的配置给出；实际使用的模型写进 Result 和案件记录，评估按它统计，
所以放在 contracts。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ModelRef(BaseModel):
    """一个模型：厂商和模型名都相同就是同一个模型。可以做 dict 的键。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    # 模型出自哪家公司，不是调用的平台；转成小写，厂商校验按它比较
    vendor: Annotated[str, StringConstraints(to_lower=True, min_length=1)]
    name: str = Field(min_length=1)  # 大小写不变：厂商的接口区分大小写

    def __str__(self) -> str:
        return f"{self.vendor}/{self.name}"


class ModelUse(BaseModel):
    """实际使用的模型，以及它是不是这个 agent 的备用模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: ModelRef
    fallback: bool  # 不是这个 agent 的主模型
