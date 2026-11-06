"""把模型调用的异常归成网关的调用错误，见 model-adapter spec「调用错误」和 add-model-adapter 的
design D7、D8。
"""

from typing import Final

from pydantic_ai.exceptions import (
    ModelAuthenticationError,
    ModelConnectionError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelRequestTimeout,
    ModelServerError,
    ModelTimeoutError,
    ModelUnavailableError,
)

from tradingagentspro.contracts.model import TokenUsage
from tradingagentspro.modules.gateway import CallError

# 按顺序匹配：ModelTimeoutError 是 ModelConnectionError 的子类，要排在前面
_KINDS: Final[tuple[tuple[tuple[type[Exception], ...], CallError], ...]] = (
    ((TimeoutError, ModelTimeoutError, ModelRequestTimeout), CallError.TIMEOUT),
    ((ModelConnectionError,), CallError.CONNECTION),
    ((ModelRateLimitError,), CallError.RATE_LIMITED),
    ((ModelQuotaExceededError,), CallError.QUOTA_EXHAUSTED),
    ((ModelAuthenticationError,), CallError.AUTH_FAILED),
    ((ModelServerError, ModelUnavailableError), CallError.SERVER_ERROR),
)


def call_error(error: Exception) -> CallError:
    """这个异常是哪一种调用错误；不在表里的是其他调用错误。"""
    for kinds, kind in _KINDS:
        if isinstance(error, kinds):
            return kind
    return CallError.OTHER


class InvalidOutput(RuntimeError):
    """模型回答了，但不符合输出类型：不是调用错误，带回答的原文、原因和这次的用量，由调用方处理。"""

    def __init__(self, text: str, reason: str, usage: TokenUsage) -> None:
        super().__init__("模型的输出不合法")
        self.text = text
        self.reason = reason
        self.usage = usage
