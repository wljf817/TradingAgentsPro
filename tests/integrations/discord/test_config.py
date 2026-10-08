"""Discord 配置的测试。场景见 openspec 的 discord-notification spec「Discord 配置」。"""

from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from tradingagentspro.integrations.discord import DiscordConfig

TOKEN = "abc.def.ghi"  # 假的 token，只用来检查它不外露

FULL_ENV = {
    "DISCORD_BOT_TOKEN": TOKEN,
    "DISCORD_GUILD_ID": "3003",
    "DISCORD_CHANNEL_ID": "2002",
    "DISCORD_USER_ID": "1001",
}


def _error_text(environ: dict[str, str]) -> str:
    with pytest.raises(ValueError) as error:
        DiscordConfig.from_env(environ)
    return str(error.value)


def test_full_environment() -> None:
    """spec「4 个环境变量齐全」。"""
    config = DiscordConfig.from_env(FULL_ENV)

    assert config.token.get_secret_value() == TOKEN
    assert (config.guild_id, config.channel_id, config.user_id) == (3003, 2002, 1001)
    assert config.connect_timeout == timedelta(seconds=30)
    assert config.send_timeout == timedelta(seconds=10)


def test_missing_variables_are_all_listed() -> None:
    """spec「缺少环境变量」：没有的和空字符串都算缺少，一次列出。"""
    environ = {**FULL_ENV, "DISCORD_USER_ID": ""}
    del environ["DISCORD_GUILD_ID"]

    text = _error_text(environ)

    assert "DISCORD_GUILD_ID" in text
    assert "DISCORD_USER_ID" in text
    leaked = TOKEN in text
    assert not leaked


@pytest.mark.parametrize("value", ["general", "0", "-5", "12.5", " 2002"])
def test_id_must_be_a_positive_integer(value: str) -> None:
    """spec「ID 不是正整数」。"""
    text = _error_text({**FULL_ENV, "DISCORD_CHANNEL_ID": value})

    assert "DISCORD_CHANNEL_ID" in text
    leaked = TOKEN in text
    assert not leaked


def test_token_is_not_exposed() -> None:
    """spec「token 不外露」：配置的字符串形式、JSON 和各种错误信息里都没有 token。"""
    config = DiscordConfig.from_env(FULL_ENV)
    texts = [
        repr(config),
        str(config),
        config.model_dump_json(),
        _error_text({**FULL_ENV, "DISCORD_GUILD_ID": ""}),
        _error_text({**FULL_ENV, "DISCORD_USER_ID": "me"}),
    ]
    with pytest.raises(ValidationError) as error:
        DiscordConfig(token=SecretStr(TOKEN), guild_id=0, channel_id=2002, user_id=1001)
    texts.append(str(error.value))

    leaked = any(TOKEN in text for text in texts)
    assert not leaked


@pytest.mark.parametrize(
    "changes",
    [
        {"connect_timeout": timedelta(0)},
        {"send_timeout": timedelta(0)},
        {"send_timeout": timedelta(seconds=-1)},
        {"token": SecretStr("")},
        {"user_id": 0},
    ],
)
def test_invalid_config_is_an_error(changes: dict[str, Any]) -> None:
    """spec「超时不是正数」，以及直接构造时 token 为空、ID 不是正数。"""
    fields: dict[str, Any] = {
        "token": SecretStr(TOKEN),
        "guild_id": 3003,
        "channel_id": 2002,
        "user_id": 1001,
    }
    with pytest.raises(ValidationError):
        DiscordConfig(**{**fields, **changes})
