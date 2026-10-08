"""环境变量的测试。场景见 openspec 的 bootstrap spec「环境变量」。"""

import pytest

from tradingagentspro.bootstrap.settings import (
    DATABASE_URL,
    VARIABLES,
    SettingsError,
    load_settings,
)

# 假的密钥，只用来检查它们不外露
SECRETS = ("apca-secret-7x1q", "discord-token-5k2w", "db-password-9f3k")

FULL_ENV = {
    DATABASE_URL: f"postgresql://trader:{SECRETS[2]}@db.local:5432/trading",
    "APCA_API_KEY_ID": "PK-TEST-ID",
    "APCA_API_SECRET_KEY": SECRETS[0],
    "DISCORD_BOT_TOKEN": SECRETS[1],
    "DISCORD_GUILD_ID": "3003",
    "DISCORD_CHANNEL_ID": "2002",
    "DISCORD_USER_ID": "1001",
}


def _error_text(environ: dict[str, str]) -> str:
    with pytest.raises(SettingsError) as error:
        load_settings(environ)
    return str(error.value)


def _leaks(text: str) -> bool:
    return any(secret in text for secret in SECRETS)


def test_variables_are_the_seven_of_the_table() -> None:
    """spec「环境变量」的表：7 个变量，数据库地址在最前面。"""
    assert VARIABLES == (
        "TRADINGAGENTSPRO_DATABASE_URL",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "DISCORD_BOT_TOKEN",
        "DISCORD_GUILD_ID",
        "DISCORD_CHANNEL_ID",
        "DISCORD_USER_ID",
    )


def test_all_given() -> None:
    """spec「全部给出」：七个变量都用上。"""
    settings = load_settings(FULL_ENV)

    assert settings.alpaca.key_id == "PK-TEST-ID"
    assert settings.alpaca.secret_key.get_secret_value() == SECRETS[0]
    assert settings.discord.token.get_secret_value() == SECRETS[1]
    discord = settings.discord
    assert (discord.guild_id, discord.channel_id, discord.user_id) == (3003, 2002, 1001)
    assert settings.database_url.get_secret_value() == (
        f"postgresql+psycopg://trader:{SECRETS[2]}@db.local:5432/trading"
    )


def test_all_missing_variables_are_listed_at_once() -> None:
    """spec「一次列出全部缺少的变量」：没有和空字符串都算缺少，按表的顺序列在一条里。"""
    environ = {**FULL_ENV, "DISCORD_USER_ID": ""}
    del environ["APCA_API_SECRET_KEY"]

    text = _error_text(environ)

    assert "APCA_API_SECRET_KEY" in text
    assert "DISCORD_USER_ID" in text
    assert text.index("APCA_API_SECRET_KEY") < text.index("DISCORD_USER_ID")
    assert not _leaks(text)


def test_nothing_given() -> None:
    """spec「一次列出全部缺少的变量」：一个都没有时列出全部 7 个。"""
    text = _error_text({})

    assert all(name in text for name in VARIABLES)


@pytest.mark.parametrize("value", ["general", "0", "-5"])
def test_discord_id_must_be_a_positive_integer(value: str) -> None:
    """spec「Discord 的 ID 不是正整数」：写明变量名，没有它的值。"""
    text = _error_text({**FULL_ENV, "DISCORD_CHANNEL_ID": value})

    assert "DISCORD_CHANNEL_ID" in text
    assert "正整数" in text
    assert not _leaks(text)


@pytest.mark.parametrize("value", ["pw-9f3k@db", "postgresql://u:pw-9f3k@db:port/x"])
def test_database_url_that_is_not_an_address(value: str) -> None:
    """spec「数据库地址写错」：写明变量名，错误信息里没有地址的任何部分。"""
    with pytest.raises(SettingsError) as error:
        load_settings({**FULL_ENV, DATABASE_URL: value})

    text = str(error.value)
    assert DATABASE_URL in text
    assert "pw-9f3k" not in text
    assert error.value.__suppress_context__ or error.value.__context__ is None


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://u:p@h:5432/d",
        "postgresql+psycopg://u:p@h:5432/d",
        "postgresql+psycopg2://u:p@h:5432/d",
    ],
)
def test_driver_is_always_psycopg(value: str) -> None:
    """spec「数据库地址不写驱动」：驱动一律换成 postgresql+psycopg，其余不变。"""
    settings = load_settings({**FULL_ENV, DATABASE_URL: value})

    assert settings.database_url.get_secret_value() == "postgresql+psycopg://u:p@h:5432/d"


def test_secrets_are_not_in_repr_or_label() -> None:
    """spec「环境变量」：日志里只能出现密码换成 *** 的数据库地址。"""
    settings = load_settings(FULL_ENV)

    assert not _leaks(repr(settings))
    assert not _leaks(str(settings))
    assert settings.database_label == "postgresql+psycopg://trader:***@db.local:5432/trading"
