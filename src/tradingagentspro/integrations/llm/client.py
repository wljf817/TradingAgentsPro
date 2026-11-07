"""向模型询问或运行一次：Anthropic 和 OpenAI 兼容两种接口，见 model-adapter spec、add-model-adapter
的 design D3 到 D8 和 update-model-tool-loop 的 design。

全部调用走一个后台事件循环：每个线程各自跑事件循环时，共用的连接会出错。一次询问只发一个请求，
一次运行可以发多个请求：模型调用读取工具，代码执行后交回，直到模型交出结果。重试和切换模型由网关
负责。
"""

import asyncio
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any, Final

import pydantic_ai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai import (
    Agent,
    AgentRetries,
    NativeOutput,
    PromptedOutput,
    Tool,
    ToolOutput,
    UsageLimits,
    capture_run_messages,
)
from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.exceptions import (
    ContentFilterError,
    ModelAPIError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext, ToolDefinition

from tradingagentspro.contracts.model import ModelRef, TokenUsage
from tradingagentspro.integrations.llm.config import Endpoint, Interface, LlmConfig
from tradingagentspro.integrations.llm.errors import InvalidOutput, call_error
from tradingagentspro.modules.gateway import CallError, ModelCallError, ModelReply

pydantic_ai.BANNER_ENABLED = False  # 第一次运行时的横幅，见 bootstrap spec「日志」

# 工具次数用完以后，再调用读取工具时交回模型的结果，见 model-adapter spec「工具次数上限」
_TOOL_LIMIT_NOTICE: Final = (
    "工具调用次数已用完，不能再查。现在用已有内容交出结果，并写明哪些数据没拿到。"
)
_REQUESTS_AFTER_TOOLS: Final = 2  # 用满工具次数以后最多再发的请求：看到提示一次，收尾一次
_NO_RETRIES: Final[AgentRetries] = {"output": 0, "tools": 0}  # 重试由网关负责
_NO_REPLY_TOOL: Final = "没有调用交回工具"  # 有交回工具时只回文字或者回答为空的原因


@dataclass(frozen=True, slots=True)
class ReadTool:
    """读取工具：模型调用，代码执行，结果交回模型。参数的格式取自 function 的签名。"""

    name: str
    description: str
    function: Callable[..., str]


@dataclass(frozen=True, slots=True)
class ReplyTool[T]:
    """交回工具：模型调用它交出结果，参数就是 output_type。"""

    name: str
    description: str
    output_type: type[T]


@dataclass
class _ToolLimit(AbstractCapability[None]):
    """一次运行的读取工具次数：用完以后不执行，只回提示，见 update-model-tool-loop 的 design D4。

    交回工具不算。检查和计数之间没有 await，一个回答里的几次调用同时执行也不会多算或少算。
    """

    limit: int
    used: int = 0

    async def wrap_tool_execute(
        self,
        ctx: RunContext[None],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        if tool_def.kind == "function":
            if self.used >= self.limit:
                return _TOOL_LIMIT_NOTICE
            self.used += 1
        return await handler(args)


class LlmClient:
    """向各厂商的模型询问或运行。可以同时调用；用完调用 close。"""

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
        interface = self._endpoints[model.vendor].interface
        agent: Agent[None, Any] = Agent(
            self._model(model),
            instructions=instructions,
            output_type=_output(interface, output_type),
            retries=_NO_RETRIES,
        )
        return self._wait(_attempt(agent, self._settings(), timeout, question, (), None))

    def run[T](
        self,
        model: ModelRef,
        timeout: timedelta,
        instructions: str,
        question: str | None,
        replies: Sequence[ReplyTool[T]],
        tools: Sequence[ReadTool] = (),
        tool_limit: int = 0,
        history: Sequence[ModelMessage] = (),
    ) -> ModelReply[T]:
        """运行一次，模型调用交回工具交出结果。只回文字时抛 InvalidOutput，其他同 ask。

        question 为 None 时请求只有之前的对话，模型接着回答。
        """
        outputs = [
            ToolOutput(reply.output_type, name=reply.name, description=reply.description)
            for reply in replies
        ]
        reply = self._run(
            model, timeout, instructions, question, [*outputs, str], tools, tool_limit, history
        )
        return self._wait(_require_tool_output(reply))

    def run_text(
        self,
        model: ModelRef,
        timeout: timedelta,
        instructions: str,
        question: str,
        tools: Sequence[ReadTool],
        tool_limit: int,
        history: Sequence[ModelMessage] = (),
    ) -> ModelReply[str]:
        """运行一次，模型用文字交出结果，其他同 ask。"""
        reply = self._run(model, timeout, instructions, question, str, tools, tool_limit, history)
        return self._wait(reply)

    def close(self) -> None:
        """取消还没有结果的询问，关掉各厂商的连接和后台事件循环。"""
        asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    def _wait[T](self, attempt: Coroutine[Any, Any, ModelReply[T]]) -> ModelReply[T]:
        return asyncio.run_coroutine_threadsafe(attempt, self._loop).result()

    def _settings(self) -> ModelSettings:
        return ModelSettings(max_tokens=self._config.max_tokens)

    def _run(
        self,
        model: ModelRef,
        timeout: timedelta,
        instructions: str,
        question: str | None,
        output_type: Any,
        tools: Sequence[ReadTool],
        tool_limit: int,
        history: Sequence[ModelMessage],
    ) -> Coroutine[Any, Any, ModelReply[Any]]:
        agent: Agent[None, Any] = Agent(
            self._model(model),
            instructions=instructions,
            output_type=output_type,
            tools=[
                Tool(item.function, name=item.name, description=item.description, max_retries=0)
                for item in tools
            ],
            retries=_NO_RETRIES,
            capabilities=[_ToolLimit(tool_limit)],
        )
        limits = UsageLimits(request_limit=tool_limit + _REQUESTS_AFTER_TOOLS)
        return _attempt(agent, self._settings(), timeout, question, history, limits)

    def _model(self, model: ModelRef) -> Model:
        client = self._clients[model.vendor]
        if isinstance(client, AsyncAnthropic):
            return AnthropicModel(
                model.name,
                provider=AnthropicProvider(anthropic_client=client),
                profile=_json_schema_profile,
            )
        return OpenAIChatModel(
            model.name,
            provider=OpenAIProvider(openai_client=client),
            profile=_reasoning_field_profile,
        )

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


def _reasoning_field_profile(profile: ModelProfile) -> ModelProfile:
    """OpenAI 接口的工具调用带空的思考字段：DeepSeek 开思考时要求，见 add-pm-calls design D10。"""
    return merge_profile(
        profile,
        OpenAIModelProfile(
            supports_thinking=True,
            thinking_enabled_by_default=True,
            openai_chat_thinking_field="reasoning_content",
            openai_chat_send_back_thinking_parts="field",
            openai_chat_thinking_requires_field_on_tool_calls=True,
        ),
    )


def _output(interface: Interface, output_type: type[Any]) -> Any:
    """文本原样要；结构化输出按接口选做法，见 add-model-adapter 的 design D5。"""
    if output_type is str:
        return str
    if interface is Interface.ANTHROPIC:
        return NativeOutput(output_type)
    return PromptedOutput(output_type)


async def _attempt(
    agent: Agent[None, Any],
    settings: ModelSettings,
    timeout: timedelta,
    question: str | None,
    history: Sequence[ModelMessage],
    limits: UsageLimits | None,
) -> ModelReply[Any]:
    """一次尝试：给出输出和各请求的用量之和，失败时抛 ModelCallError 或 InvalidOutput。"""
    with capture_run_messages() as captured:
        new = partial(_new_messages, captured, len(history))
        try:
            async with asyncio.timeout(timeout.total_seconds()):
                result = await agent.run(
                    question,
                    message_history=history,
                    model_settings=settings,
                    usage_limits=limits,
                )
        except ContentFilterError as error:  # 模型拒绝回答，排在它的父类前面
            raise ModelCallError(CallError.OTHER, _spent(new()), _transcript(captured)) from error
        except (UnexpectedModelBehavior, UsageLimitExceeded) as error:
            raise _invalid_output(new(), error, _transcript(captured)) from error
        except (TimeoutError, ModelAPIError) as error:
            raise ModelCallError(call_error(error), _spent(new()), _transcript(captured)) from error
        return ModelReply(
            result.output, _usage(result.usage), result.response.model_name, _transcript(captured)
        )


async def _require_tool_output[T](
    attempt: Coroutine[Any, Any, ModelReply[T | str]],
) -> ModelReply[T]:
    """有交回工具时只回文字算输出不合法，见 update-model-tool-loop 的 design D3。"""
    reply = await attempt
    if isinstance(reply.output, str):
        assert reply.transcript is not None
        raise InvalidOutput(reply.output, _NO_REPLY_TOOL, reply.usage, reply.transcript)
    return ModelReply(reply.output, reply.usage, reply.served_model, reply.transcript)


def _transcript(captured: Sequence[ModelMessage]) -> str:
    """这次尝试的全部消息，见 add-model-call-trace 的 design D4。"""
    return ModelMessagesTypeAdapter.dump_json(list(captured)).decode()


def _new_messages(captured: Sequence[ModelMessage], history_length: int) -> Sequence[ModelMessage]:
    """这次运行的消息：捕获的消息里排在前面的是之前的对话。"""
    return captured[history_length:]


def _usage(usage: Any) -> TokenUsage:
    return TokenUsage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


def _responses(messages: Sequence[ModelMessage]) -> list[ModelResponse]:
    return [item for item in messages if isinstance(item, ModelResponse)]


def _spent(messages: Sequence[ModelMessage]) -> TokenUsage | None:
    """已经拿到回答的各请求的用量之和；一个回答都没有时为 None。"""
    responses = _responses(messages)
    if not responses:
        return None
    return TokenUsage(
        input_tokens=sum(item.usage.input_tokens for item in responses),
        output_tokens=sum(item.usage.output_tokens for item in responses),
    )


def _invalid_output(
    messages: Sequence[ModelMessage], error: Exception, transcript: str
) -> InvalidOutput:
    """模型最后一个回答的原文、原因和各请求的用量之和：工具调用的原文是它的参数，原因是校验的错误。"""
    parts = _responses(messages)[-1].parts
    text = "".join(part.content for part in parts if isinstance(part, TextPart))
    arguments = (part.args_as_json_str() for part in parts if isinstance(part, ToolCallPart))
    original = text or "".join(arguments)
    reason = str(error.__cause__ or error) if original else _NO_REPLY_TOOL
    spent = _spent(messages)
    assert spent is not None
    return InvalidOutput(original, reason, spent, transcript)
