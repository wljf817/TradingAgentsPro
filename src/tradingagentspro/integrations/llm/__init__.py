"""真实模型的适配器：Anthropic 和 OpenAI 兼容两种接口，按厂商分派到替身或真实模型，见 openspec 的
model-adapter spec。装配时用 VendorRouter 作为网关的 ModelBackend；LlmClient 用完要 close。
"""

from tradingagentspro.integrations.llm.backend import LlmBackend
from tradingagentspro.integrations.llm.client import LlmClient, ReadTool, ReplyTool
from tradingagentspro.integrations.llm.config import Endpoint, Interface, LlmConfig
from tradingagentspro.integrations.llm.errors import InvalidOutput
from tradingagentspro.integrations.llm.filings import filing_tools
from tradingagentspro.integrations.llm.financials import financials_tools
from tradingagentspro.integrations.llm.news import news_tools
from tradingagentspro.integrations.llm.router import VendorRouter
from tradingagentspro.integrations.llm.sub_agent import (
    RecordEntries,
    SubAgentSetup,
    ToolBuilder,
    combined,
    no_tools,
)
from tradingagentspro.integrations.llm.valuation import valuation_tools

__all__ = [
    "Endpoint",
    "Interface",
    "InvalidOutput",
    "LlmBackend",
    "LlmClient",
    "LlmConfig",
    "ReadTool",
    "RecordEntries",
    "ReplyTool",
    "SubAgentSetup",
    "ToolBuilder",
    "VendorRouter",
    "combined",
    "filing_tools",
    "financials_tools",
    "news_tools",
    "no_tools",
    "valuation_tools",
]
