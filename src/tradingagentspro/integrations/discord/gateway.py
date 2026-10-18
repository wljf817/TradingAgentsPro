"""用 discord.py 实现 Gateway：登录、注册 /plan、连接、核对频道，发消息、改消息，
把按钮按下交给 DiscordNotifier，把 /plan 交给 PlanCommand。

这里只翻译 discord.py 的对象和错误，逻辑在 notifier.py 和 commands.py。见 add-discord-notify 的
design D8 和 add-manual-plan-entry 的 design D8；要连真实的 Discord，由冒烟测试覆盖。
"""

import asyncio
import contextlib
from collections.abc import Callable, Iterator, Mapping
from typing import Any, cast

import aiohttp
import discord
from discord import app_commands

from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.integrations.discord.commands import PlanCommand
from tradingagentspro.integrations.discord.config import DiscordConfig
from tradingagentspro.integrations.discord.notifier import PressHandler
from tradingagentspro.integrations.discord.plans import SECTOR_NAMES, PlanEntry
from tradingagentspro.modules.notification import NotificationUnavailableError

# /plan 每个字段的说明，Discord 显示在字段下面（最长 100 字）
_SYMBOL = "股票代码，比如 AAPL"
_SECTOR = "板块"
# 板块的选项：Discord 只让选这 11 个，选项的值是板块 ETF 的代码
_SECTORS = [app_commands.Choice(name=name, value=etf.value) for etf, name in SECTOR_NAMES.items()]
_QUANTITY = "股数，整股"
_LIMIT = "限价，美元，比如 180.50"
_EXIT_LIMIT = "限价，美元，比如 180.50；不填就是市价单"
_STOP = "止损价，美元，比如 171.00"
_EMERGENCY = "紧急阈值，百分数，8 表示 8%；不填用默认值"
_DEVIATION = "允许价格偏离，百分数，3 表示 3%；不填用默认值"
_EXPIRES = "有效期：到这个交易日收盘，写成 2026-10-16；不填到执行交易日收盘"


@contextlib.contextmanager
def discord_errors(*, ignore_not_found: bool = False) -> Iterator[None]:
    """把 discord.py 和网络的错误转成 NotificationUnavailableError。

    信息是状态码和 Discord 给的原因，不含 token。
    ignore_not_found 为真时，Discord 回答 404（比如消息已被删掉）什么也不做。
    """
    try:
        yield
    except discord.NotFound as error:
        if not ignore_not_found:
            raise NotificationUnavailableError(f"Discord：{error}") from error
    except (discord.DiscordException, aiohttp.ClientError, OSError) as error:
        raise NotificationUnavailableError(f"Discord：{error}") from error


async def register_commands(tree: app_commands.CommandTree[Any], guild_id: int) -> None:
    """在这个服务器注册 /plan：覆盖这个机器人在该服务器的全部命令，立即生效。

    失败（比如没有 applications.commands 授权）时抛 NotificationUnavailableError。
    """
    try:
        await tree.sync(guild=discord.Object(id=guild_id))
    except (discord.DiscordException, aiohttp.ClientError, OSError) as error:
        raise NotificationUnavailableError(f"注册斜杠命令失败：{error}") from error


class DiscordGateway:
    """Discord 机器人的连接。构造时不连 Discord，run 里才登录。

    只用 guilds 这个 intent（不需要特权 intent）；按钮和斜杠命令的交互不需要额外的 intent。
    plans 处理 /plan，登录以后注册。
    """

    def __init__(self, config: DiscordConfig, plans: PlanCommand) -> None:
        self._config = config
        self._plans = plans
        self._client: _Client | None = None

    async def run(self, on_press: PressHandler, ready: Callable[[], None]) -> None:
        """登录、注册 /plan、连接、核对频道，然后调用 ready，一直运行到 close。

        token 无效、注册失败、频道不对、连接中止时抛 NotificationUnavailableError。
        """
        client = _Client(on_press, self._plans, self._config.guild_id)
        self._client = client
        async with client:
            try:
                await client.login(self._config.token.get_secret_value())
            except discord.LoginFailure as error:
                raise NotificationUnavailableError("Discord 登录失败：token 无效") from error
            except (discord.DiscordException, aiohttp.ClientError, OSError) as error:
                raise NotificationUnavailableError(f"Discord 登录失败：{error}") from error
            await register_commands(client.tree, self._config.guild_id)
            connection = asyncio.create_task(client.connect(reconnect=True))
            try:
                if await self._ready(client, connection):
                    await self._check_channel(client)
                    ready()
                with discord_errors():
                    await connection
            finally:
                if not connection.done():
                    await client.close()
                    with contextlib.suppress(Exception):
                        await connection

    async def send(
        self,
        content: str,
        view: discord.ui.View | None,
        mentions: discord.AllowedMentions,
    ) -> int:
        channel = self._channel()
        with discord_errors():
            if view is None:
                message = await channel.send(content, allowed_mentions=mentions)
            else:
                message = await channel.send(content, view=view, allowed_mentions=mentions)
        return message.id

    async def edit(self, message_id: int, embed: discord.Embed) -> None:
        message = self._channel().get_partial_message(message_id)
        with discord_errors(ignore_not_found=True):
            await message.edit(embed=embed, view=None)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    def _channel(self) -> discord.PartialMessageable:
        """配置的频道：只用 HTTP 发消息，不需要缓存。"""
        if self._client is None:
            raise NotificationUnavailableError("Discord 还没连接")
        return self._client.get_partial_messageable(
            self._config.channel_id, guild_id=self._config.guild_id, type=discord.ChannelType.text
        )

    @staticmethod
    async def _ready(client: discord.Client, connection: asyncio.Task[None]) -> bool:
        """等到连上；连接先结束了（出错）时为 False，错误由调用方 await connection 取出。"""
        waiting = asyncio.create_task(client.wait_until_ready())
        await asyncio.wait({waiting, connection}, return_when=asyncio.FIRST_COMPLETED)
        if waiting.done():
            return True
        waiting.cancel()
        return False

    async def _check_channel(self, client: discord.Client) -> None:
        """配置的频道存在、机器人看得到，而且是配置的服务器里的文字频道。"""
        channel_id, guild_id = self._config.channel_id, self._config.guild_id
        try:
            channel = await client.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden) as error:
            raise NotificationUnavailableError(
                f"频道 {channel_id} 不存在，或者机器人看不到它：{error}"
            ) from error
        except (discord.DiscordException, aiohttp.ClientError, OSError) as error:
            raise NotificationUnavailableError(f"核对频道 {channel_id} 时出错：{error}") from error
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
            raise NotificationUnavailableError(
                f"频道 {channel_id} 不是服务器 {guild_id} 里的文字频道"
            )


def plan_tree(
    client: discord.Client, plans: PlanCommand, guild_id: int
) -> app_commands.CommandTree[Any]:
    """client 的命令树：只有加在这个服务器下的 /plan，没有全局命令。"""
    tree: app_commands.CommandTree[Any] = app_commands.CommandTree(client)
    tree.add_command(PlanGroup(plans), guild=discord.Object(id=guild_id))
    return tree


class _Client(discord.Client):
    """按钮的交互交给 on_press；斜杠命令由命令树交给 PlanGroup，on_interaction 不处理。"""

    def __init__(self, on_press: PressHandler, plans: PlanCommand, guild_id: int) -> None:
        super().__init__(intents=discord.Intents(guilds=True))
        self._on_press = on_press
        self.tree = plan_tree(self, plans, guild_id)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is discord.InteractionType.component and interaction.message:
            await self._on_press(InteractionPress(interaction))


class InteractionPress:
    """一次按钮按下：把 discord.Interaction 翻译成 ButtonPress。"""

    def __init__(self, interaction: discord.Interaction) -> None:
        self._interaction = interaction

    @property
    def user_id(self) -> int:
        return self._interaction.user.id

    @property
    def custom_id(self) -> str:
        data = cast(Mapping[str, Any], self._interaction.data or {})
        return str(data.get("custom_id", ""))

    @property
    def message_id(self) -> int:
        message = self._interaction.message
        assert message is not None  # _Client 只交出带消息的按钮交互
        return message.id

    async def defer(self) -> None:
        await self._interaction.response.defer()

    async def reply(self, text: str) -> None:
        if self._interaction.response.is_done():
            await self._interaction.followup.send(text, ephemeral=True)
        else:
            await self._interaction.response.send_message(text, ephemeral=True)

    async def show(self, embed: discord.Embed) -> None:
        if self._interaction.response.is_done():
            await self._interaction.edit_original_response(embed=embed, view=None)
        else:
            await self._interaction.response.edit_message(embed=embed, view=None)


class InteractionCall:
    """一次 /plan：把 discord.Interaction 翻译成 CommandCall。回复都只有本人看得到。"""

    def __init__(self, interaction: discord.Interaction) -> None:
        self._interaction = interaction

    @property
    def user_id(self) -> int:
        return self._interaction.user.id

    async def refuse(self, text: str) -> None:
        await self._interaction.response.send_message(text, ephemeral=True)

    async def defer(self) -> None:
        await self._interaction.response.defer(ephemeral=True, thinking=True)

    async def reply(self, text: str) -> None:
        await self._interaction.edit_original_response(content=text)


class PlanGroup(app_commands.Group):
    """/plan 的四个子命令：只声明这个动作允许的字段，必填项和股数的最小值由 Discord 检查。

    见 manual-plans spec「子命令和字段」。discord.py 解析好参数以后，装成 PlanEntry
    交给 PlanCommand。
    """

    def __init__(self, plans: PlanCommand) -> None:
        super().__init__(name="plan", description="手工录入交易方案")
        self._plans = plans

    @app_commands.command(name="buy", description="买入：新建仓，限价单")
    @app_commands.choices(sector=_SECTORS)
    @app_commands.describe(
        symbol=_SYMBOL,
        sector=_SECTOR,
        quantity=_QUANTITY,
        limit=_LIMIT,
        stop=_STOP,
        emergency=_EMERGENCY,
        deviation=_DEVIATION,
        expires=_EXPIRES,
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        symbol: str,
        sector: app_commands.Choice[str],
        quantity: app_commands.Range[int, 1],
        limit: str,
        stop: str,
        emergency: str | None = None,
        deviation: str | None = None,
        expires: str | None = None,
    ) -> None:
        entry = PlanEntry(
            TradeAction.BUY,
            symbol,
            sector.value,
            quantity,
            limit,
            stop,
            emergency,
            deviation,
            expires,
        )
        await self._plans.handle(InteractionCall(interaction), entry)

    @app_commands.command(name="add", description="加仓：限价单")
    @app_commands.choices(sector=_SECTORS)
    @app_commands.describe(
        symbol=_SYMBOL,
        sector=_SECTOR,
        quantity=_QUANTITY,
        limit=_LIMIT,
        stop=_STOP,
        emergency=_EMERGENCY,
        deviation=_DEVIATION,
        expires=_EXPIRES,
    )
    async def add(
        self,
        interaction: discord.Interaction,
        symbol: str,
        sector: app_commands.Choice[str],
        quantity: app_commands.Range[int, 1],
        limit: str,
        stop: str,
        emergency: str | None = None,
        deviation: str | None = None,
        expires: str | None = None,
    ) -> None:
        entry = PlanEntry(
            TradeAction.ADD,
            symbol,
            sector.value,
            quantity,
            limit,
            stop,
            emergency,
            deviation,
            expires,
        )
        await self._plans.handle(InteractionCall(interaction), entry)

    @app_commands.command(name="reduce", description="减仓：卖出一部分，板块取持仓的")
    @app_commands.describe(symbol=_SYMBOL, quantity=_QUANTITY, limit=_EXIT_LIMIT, expires=_EXPIRES)
    async def reduce(
        self,
        interaction: discord.Interaction,
        symbol: str,
        quantity: app_commands.Range[int, 1],
        limit: str | None = None,
        expires: str | None = None,
    ) -> None:
        entry = PlanEntry(
            TradeAction.REDUCE, symbol, quantity=quantity, limit=limit, expires=expires
        )
        await self._plans.handle(InteractionCall(interaction), entry)

    @app_commands.command(name="close", description="清仓：卖出全部可卖股数，板块取持仓的")
    @app_commands.describe(symbol=_SYMBOL, limit=_EXIT_LIMIT, expires=_EXPIRES)
    async def close(
        self,
        interaction: discord.Interaction,
        symbol: str,
        limit: str | None = None,
        expires: str | None = None,
    ) -> None:
        entry = PlanEntry(TradeAction.CLOSE, symbol, limit=limit, expires=expires)
        await self._plans.handle(InteractionCall(interaction), entry)
