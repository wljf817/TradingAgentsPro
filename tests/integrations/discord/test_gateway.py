"""DiscordGateway 的错误转换：discord.py 和网络的错误一律转成通知不可用。

见 add-discord-notify 的 design D8。连接、发消息和按钮的应答要连真实的 Discord，由冒烟测试覆盖。
"""

from types import SimpleNamespace
from typing import Any

import aiohttp
import discord
import pytest
from pydantic import SecretStr

from tests.modules.notification.builders import CHANNEL_ID, GUILD_ID, USER_ID
from tradingagentspro.integrations.discord import DiscordConfig, DiscordGateway
from tradingagentspro.integrations.discord.gateway import discord_errors
from tradingagentspro.integrations.discord.notifier import Gateway
from tradingagentspro.modules.notification import NotificationUnavailableError


def _response(status: int, reason: str) -> Any:
    """discord.py 的 HTTPException 只用到响应的状态码和原因。"""
    return SimpleNamespace(status=status, reason=reason)


def test_satisfies_the_protocol() -> None:
    """DiscordGateway 满足 Gateway；构造时不连 Discord。"""
    config = DiscordConfig(
        token=SecretStr("test-token"), guild_id=GUILD_ID, channel_id=CHANNEL_ID, user_id=USER_ID
    )
    gateway: Gateway = DiscordGateway(config)
    assert gateway is not None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (discord.DiscordServerError(_response(503, "Service Unavailable"), ""), "503"),
        (
            discord.Forbidden(
                _response(403, "Forbidden"), {"code": 50013, "message": "Missing Permissions"}
            ),
            "Missing Permissions",
        ),
        (discord.NotFound(_response(404, "Not Found"), "Unknown Channel"), "Unknown Channel"),
        (aiohttp.ClientConnectionError("连不上 discord.com"), "连不上 discord.com"),
        (TimeoutError("读超时"), "读超时"),
        (OSError("网络不通"), "网络不通"),
    ],
    ids=["服务器错误", "没有权限", "频道不存在", "连不上", "超时", "网络不通"],
)
def test_errors_become_unavailable(error: Exception, expected: str) -> None:
    """spec「Discord 报错」：错误信息里有状态码或 Discord 给的原因。"""
    with pytest.raises(NotificationUnavailableError, match=expected) as raised, discord_errors():
        raise error

    assert raised.value.__cause__ is error


def test_deleted_message_is_ignored_when_editing() -> None:
    """spec「消息已被删掉」：改消息时 Discord 回答 404，什么也不做。"""
    with discord_errors(ignore_not_found=True):
        raise discord.NotFound(_response(404, "Not Found"), "Unknown Message")


def test_other_errors_are_not_converted() -> None:
    """程序自己的错误不当作 Discord 不可用。"""
    with pytest.raises(ValueError, match="程序错误"), discord_errors():
        raise ValueError("程序错误")
