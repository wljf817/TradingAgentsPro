"""Discord 适配器：用 discord.py 实现通知模块的 Notifier，推送告警、请求人工确认。

见 openspec 的 discord-notification spec 和 add-discord-notify 的 design。装配时用 DiscordGateway
建一个 DiscordNotifier，先 start() 再用，退出时 close()。
"""

from tradingagentspro.integrations.discord.config import DiscordConfig
from tradingagentspro.integrations.discord.gateway import DiscordGateway
from tradingagentspro.integrations.discord.notifier import DiscordNotifier

__all__ = ["DiscordConfig", "DiscordGateway", "DiscordNotifier"]
