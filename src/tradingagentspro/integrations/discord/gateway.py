"""用 discord.py 实现 Gateway：登录、连接、核对频道，发消息、改消息，把按钮按下交出去。

这里只翻译 discord.py 的对象和错误，逻辑都在 notifier.py。见 add-discord-notify 的 design D8；
要连真实的 Discord，由冒烟测试覆盖。
"""

import asyncio
import contextlib
from collections.abc import Callable, Iterator, Mapping
from typing import Any, cast

import aiohttp
import discord

from tradingagentspro.integrations.discord.config import DiscordConfig
from tradingagentspro.integrations.discord.notifier import PressHandler
from tradingagentspro.modules.notification import NotificationUnavailableError


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


class DiscordGateway:
    """Discord 机器人的连接。构造时不连 Discord，run 里才登录。

    只用 guilds 这个 intent（不需要特权 intent）；按钮的交互不需要额外的 intent。
    """

    def __init__(self, config: DiscordConfig) -> None:
        self._config = config
        self._client: _Client | None = None

    async def run(self, on_press: PressHandler, ready: Callable[[], None]) -> None:
        """登录、连接、核对频道，然后调用 ready，一直运行到 close。

        token 无效、频道不对、连接中止时抛 NotificationUnavailableError。
        """
        client = _Client(on_press)
        self._client = client
        async with client:
            try:
                await client.login(self._config.token.get_secret_value())
            except discord.LoginFailure as error:
                raise NotificationUnavailableError("Discord 登录失败：token 无效") from error
            except (discord.DiscordException, aiohttp.ClientError, OSError) as error:
                raise NotificationUnavailableError(f"Discord 登录失败：{error}") from error
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


class _Client(discord.Client):
    """把按钮的交互交给 on_press；别的交互（比如斜杠命令）不处理。"""

    def __init__(self, on_press: PressHandler) -> None:
        super().__init__(intents=discord.Intents(guilds=True))
        self._on_press = on_press

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
