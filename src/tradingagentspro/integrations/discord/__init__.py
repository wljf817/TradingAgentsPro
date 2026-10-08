"""Discord 适配器：用 discord.py 实现通知模块的 Notifier，推送告警、请求人工确认；
接收 /plan 手工录入的方案，交给通知模块的 PlanIntake。

见 openspec 的 discord-notification spec、manual-plans spec，以及 add-discord-notify、
add-manual-plan-entry 的 design。装配时先建 PlanCommand，再用它建 DiscordGateway，
用 DiscordGateway 建一个 DiscordNotifier，先 start() 再用，退出时 close()。
"""

from tradingagentspro.integrations.discord.commands import PlanCommand
from tradingagentspro.integrations.discord.config import DiscordConfig
from tradingagentspro.integrations.discord.gateway import DiscordGateway
from tradingagentspro.integrations.discord.notifier import DiscordNotifier, Gateway

__all__ = ["DiscordConfig", "DiscordGateway", "DiscordNotifier", "Gateway", "PlanCommand"]
