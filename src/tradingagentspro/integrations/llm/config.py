"""模型接口的配置和默认值，见 model-adapter spec「两种接口」。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, SecretStr


class Interface(StrEnum):
    """适配器支持的接口：厂商用哪种写在模型文件里，见 add-model-adapter 的 design D2。"""

    ANTHROPIC = "anthropic"  # Anthropic Messages API
    OPENAI = "openai"  # OpenAI Chat Completions API


class Endpoint(BaseModel):
    """一家厂商的连接：接口、地址和 key。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interface: Interface
    base_url: str = Field(min_length=1)
    api_key: SecretStr


class LlmConfig(BaseModel):
    """适配器的配置和默认值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每次请求的输出 token 上限
    max_tokens: PositiveInt = 16_000
