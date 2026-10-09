"""模型的身份、实际使用的模型和一次尝试的 token 用量，见 model-fallback spec「模型的配置」「实际
使用的模型」和 call-budget spec「费用的算法」。

各 agent 用哪些模型由 Agent 网关的配置给出；实际使用的模型写进 Result 和案件记录，评估按它统计；
token 用量由调用方从模型的返回里取得，交给 Agent 网关算费用，也写进程序日志。所以都放在 contracts。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, StringConstraints


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


class TokenUsage(BaseModel):
    """一次尝试的 token 用量，调用方从模型的返回里取得。推理 token 算输出，缓存命中算输入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
