"""Cboe 适配器：用 Cboe 免费的 VIX 历史文件实现行情模块的 VIX 源。

见 openspec 的 market-context spec「Cboe 数据源」。
"""

from tradingagentspro.integrations.cboe.vix import CBOE_VIX_URL, CboeConfig, CboeVixSource

__all__ = ["CBOE_VIX_URL", "CboeConfig", "CboeVixSource"]
