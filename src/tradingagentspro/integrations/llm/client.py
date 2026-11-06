"""向模型问一次：Anthropic 和 OpenAI 兼容两种接口，见 model-adapter spec 和 add-model-adapter 的
design D3 到 D8。

全部询问走一个后台事件循环：每个线程各自跑事件循环时，共用的连接会出错。一次询问只发一个请求，
重试和切换模型由网关负责。
"""

import asyncio
import threading
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

import pydantic_ai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai import (
    Agent,
    NativeOutput,
    PromptedOutput,
    capture_run_messages,
)
from pydantic_ai.exceptions import ContentFilterError, ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.integrations.llm.config import Endpoint, Interface, LlmConfig
from tradingagentspro.integrations.llm.errors import InvalidOutput, call_error
from tradingagentspro.modules.gateway import CallError, ModelCallError, ModelReply

pydantic_ai.BANNER_ENABLED = False  # 第一次运行时的横幅，见 bootstrap spec「日志」


class LlmClient:
    """向各厂商的模型问一次。可以同时询问；用完调用 close。"""

    def __init__(self, endpoints: Mapping[str, Endpoint], config: LlmConfig) -> None:
        self._endpoints = endpoints
        self._config = config
        self._clients = {vendor: _sdk_client(item) for vendor, item in endpoints.items()}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="llm", daemon=True)
        self._thread.start()

    def ask[T](
        self,
        model: ModelRef,
        timeout: timedelta,
        instructions: str,
        question: str,
        output_type: type[T],
    ) -> ModelReply[T]:
        """问一次，等到结果。没有拿到回答时抛 ModelCallError，回答不合法时抛 InvalidOutput。"""
        attempt = self._attempt(model, timeout, instructions, question, output_type)
        return asyncio.run_coroutine_threadsafe(attempt, self._loop).result()

    def close(self) -> None:
        """取消还没有结果的询问，关掉各厂商的连接和后台事件循环。"""
        asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    async def _attempt[T](
        self,
        model: ModelRef,
        timeout: timedelta,
        instructions: str,
        question: str,
        output_type: type[T],
    ) -> ModelReply[T]:
        interface = self._endpoints[model.vendor].interface
        agent: Agent[None, Any] = Agent(
            self._model(model),
            instructions=instructions,
            output_type=_output(interface, output_type),
            retries={"output": 0},
        )
        settings = ModelSettings(max_tokens=self._config.max_tokens)
        with capture_run_messages() as messages:
            try:
                async with asyncio.timeout(timeout.total_seconds()):
                    result = await agent.run(question, model_settings=settings)
            except ContentFilterError as error:  # 模型拒绝回答，排在它的父类前面
                raise ModelCallError(CallError.OTHER) from error
            except UnexpectedModelBehavior as error:
                raise _invalid_output(messages) from error
            except (TimeoutError, ModelAPIError) as error:
                raise ModelCallError(call_error(error)) from error
        return ModelReply(result.output, _usage(result.usage))

    def _model(self, model: ModelRef) -> Model:
        client = self._clients[model.vendor]
        if isinstance(client, AsyncAnthropic):
            return AnthropicModel(
                model.name,
                provider=AnthropicProvider(anthropic_client=client),
                profile=_json_schema_profile,
            )
        return OpenAIChatModel(model.name, provider=OpenAIProvider(openai_client=client))

    async def _shutdown(self) -> None:
        running = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        for client in self._clients.values():
            await client.close()


def _sdk_client(endpoint: Endpoint) -> AsyncAnthropic | AsyncOpenAI:
    """厂商的 SDK 客户端，关掉 SDK 的重试。"""
    key = endpoint.api_key.get_secret_value()
    if endpoint.interface is Interface.ANTHROPIC:
        return AsyncAnthropic(api_key=key, base_url=endpoint.base_url, max_retries=0)
    return AsyncOpenAI(api_key=key, base_url=endpoint.base_url, max_retries=0)


def _json_schema_profile(profile: ModelProfile) -> ModelProfile:
    """Anthropic 接口一律带 JSON schema：不认识的模型名默认不支持，见 design D5。"""
    return merge_profile(profile, ModelProfile(supports_json_schema_output=True))


def _output(interface: Interface, output_type: type[Any]) -> Any:
    """文本原样要；结构化输出按接口选做法，见 add-model-adapter 的 design D5。"""
    if output_type is str:
        return str
    if interface is Interface.ANTHROPIC:
        return NativeOutput(output_type)
    return PromptedOutput(output_type)


def _usage(usage: Any) -> TokenUsage:
    return TokenUsage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


def _invalid_output(messages: Sequence[ModelMessage]) -> InvalidOutput:
    """模型回答的原文和这次的用量。"""
    response = next(item for item in reversed(messages) if isinstance(item, ModelResponse))
    text = "".join(part.content for part in response.parts if isinstance(part, TextPart))
    return InvalidOutput(text, _usage(response.usage))
