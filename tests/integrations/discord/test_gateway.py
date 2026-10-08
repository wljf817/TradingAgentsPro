"""DiscordGateway：错误转换、/plan 的定义和注册。

错误转换见 add-discord-notify 的 design D8：discord.py 和网络的错误一律转成通知不可用。
/plan 见 manual-plans spec「子命令和字段」「命令的注册」和 add-manual-plan-entry 的 design D8。
连接、发消息、注册和应答要连真实的 Discord，由冒烟测试覆盖。
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import aiohttp
import discord
import pytest
from discord import app_commands
from pydantic import SecretStr

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.ids import FakeIds
from tests.fakes.plan_intake import FakePlanIntake
from tests.modules.notification.builders import CHANNEL_ID, GUILD_ID, USER_ID
from tradingagentspro.integrations.discord import DiscordConfig, DiscordGateway, PlanCommand
from tradingagentspro.integrations.discord.gateway import (
    PlanGroup,
    _Client,
    discord_errors,
    plan_tree,
    register_commands,
)
from tradingagentspro.integrations.discord.notifier import Gateway
from tradingagentspro.modules.notification import NotificationUnavailableError


def _response(status: int, reason: str) -> Any:
    """discord.py 的 HTTPException 只用到响应的状态码和原因。"""
    return SimpleNamespace(status=status, reason=reason)


CONFIG = DiscordConfig(
    token=SecretStr("test-token"), guild_id=GUILD_ID, channel_id=CHANNEL_ID, user_id=USER_ID
)


def _plans() -> PlanCommand:
    return PlanCommand(FakePlanIntake(), FakeIds(), sample_nyse_calendar(), CONFIG)


def test_satisfies_the_protocol() -> None:
    """DiscordGateway 满足 Gateway；构造时不连 Discord。"""
    gateway: Gateway = DiscordGateway(CONFIG, _plans())
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


# /plan 的定义


def _options(name: str) -> list[tuple[str, bool, discord.AppCommandOptionType]]:
    """子命令的字段：名字、是否必填、选项类型，按定义的顺序。"""
    command = PlanGroup(_plans()).get_command(name)
    assert isinstance(command, app_commands.Command)
    return [(p.name, p.required, p.type) for p in command.parameters]


STRING = discord.AppCommandOptionType.string
INTEGER = discord.AppCommandOptionType.integer
OPENING = [
    ("symbol", True, STRING),
    ("sector", True, STRING),
    ("quantity", True, INTEGER),
    ("limit", True, STRING),
    ("stop", True, STRING),
    ("emergency", False, STRING),
    ("deviation", False, STRING),
    ("expires", False, STRING),
]


def test_group_has_four_subcommands() -> None:
    """spec「子命令和字段」：/plan 有 buy、add、reduce、close。"""
    group = PlanGroup(_plans())

    assert group.name == "plan"
    assert sorted(c.name for c in group.commands) == ["add", "buy", "close", "reduce"]


@pytest.mark.parametrize("name", ["buy", "add"])
def test_opening_fields(name: str) -> None:
    """spec「buy 的字段」「add 的字段」。"""
    assert _options(name) == OPENING


def test_reduce_fields() -> None:
    """spec「reduce 的字段」。"""
    assert _options("reduce") == [
        ("symbol", True, STRING),
        ("quantity", True, INTEGER),
        ("limit", False, STRING),
        ("expires", False, STRING),
    ]


def test_close_fields() -> None:
    """spec「close 的字段」。"""
    assert _options("close") == [
        ("symbol", True, STRING),
        ("limit", False, STRING),
        ("expires", False, STRING),
    ]


@pytest.mark.parametrize("name", ["buy", "add", "reduce", "close"])
def test_every_field_is_described(name: str) -> None:
    """spec「子命令和字段」：每个字段带中文说明；股数最小 1，由 Discord 检查。"""
    command = PlanGroup(_plans()).get_command(name)
    assert isinstance(command, app_commands.Command)

    for parameter in command.parameters:
        assert parameter.description not in ("", "…")
        assert len(parameter.description) <= 100
        if parameter.name == "quantity":
            assert parameter.min_value == 1


def test_plan_is_a_guild_command_only() -> None:
    """spec「启动时注册」：/plan 只加在配置的服务器下，不是全局命令。"""
    tree = plan_tree(discord.Client(intents=discord.Intents(guilds=True)), _plans(), GUILD_ID)

    assert [c.name for c in tree.get_commands(guild=discord.Object(id=GUILD_ID))] == ["plan"]
    assert tree.get_commands() == []


# 注册


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            discord.Forbidden(
                _response(403, "Forbidden"), {"code": 50001, "message": "Missing Access"}
            ),
            "Missing Access",
        ),
        (discord.DiscordServerError(_response(503, "Service Unavailable"), ""), "503"),
        (aiohttp.ClientConnectionError("连不上 discord.com"), "连不上 discord.com"),
    ],
    ids=["没有授权", "服务器错误", "连不上"],
)
def test_registration_errors_become_unavailable(error: Exception, expected: str) -> None:
    """spec「注册被拒」：信息里有「注册斜杠命令失败」和 Discord 给的原因。"""
    tree = plan_tree(discord.Client(intents=discord.Intents(guilds=True)), _plans(), GUILD_ID)
    guilds: list[int] = []

    async def sync(*, guild: discord.abc.Snowflake | None = None) -> list[Any]:
        assert guild is not None
        guilds.append(guild.id)
        raise error

    tree.sync = sync  # type: ignore[method-assign]

    with pytest.raises(NotificationUnavailableError, match="注册斜杠命令失败") as raised:
        asyncio.run(register_commands(tree, GUILD_ID))

    assert expected in str(raised.value)
    assert guilds == [GUILD_ID]


def test_definition_converts_to_the_registration_payload() -> None:
    """注册时发给 Discord 的数据：名字、说明和选项都能转出来，必填项排在可选项前面。"""
    client = discord.Client(intents=discord.Intents(guilds=True))
    tree = plan_tree(client, _plans(), GUILD_ID)
    [group] = tree.get_commands(guild=discord.Object(id=GUILD_ID))

    payload = group.to_dict(tree)

    assert payload["name"] == "plan"
    subcommands = {o["name"]: o for o in payload["options"]}
    assert sorted(subcommands) == ["add", "buy", "close", "reduce"]
    for subcommand in subcommands.values():
        required = [o["required"] for o in subcommand["options"]]
        assert required == sorted(required, reverse=True)


# 和确认按钮互不干扰


def _routed(kind: discord.InteractionType) -> list[object]:
    """把一次交互交给 _Client.on_interaction，返回交给按钮处理的那些。"""
    pressed: list[object] = []

    async def on_press(press: object) -> None:
        pressed.append(press)

    client = _Client(on_press, _plans(), GUILD_ID)
    interaction: Any = SimpleNamespace(type=kind, message=SimpleNamespace(id=1))
    asyncio.run(client.on_interaction(interaction))
    return pressed


def test_plan_does_not_reach_the_buttons() -> None:
    """spec「收到 /plan」：斜杠命令不交给按钮的处理，由命令树交给 PlanGroup。"""
    assert _routed(discord.InteractionType.application_command) == []


def test_buttons_still_reach_the_buttons() -> None:
    """spec「按下确认按钮」：按钮照旧交给按钮的处理；命令树只接斜杠命令（discord.py 的分流）。"""
    assert len(_routed(discord.InteractionType.component)) == 1
