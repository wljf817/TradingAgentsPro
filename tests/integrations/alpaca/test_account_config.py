"""账户设置的测试：不做空、最大保证金倍数 1，按现金账户使用。

场景见 alpaca-broker spec「账户设置」，Alpaca 由 FakeAlpaca 模拟。
"""

import httpx
import pytest
from pydantic import SecretStr

from tests.integrations.alpaca.server import FakeAlpaca
from tradingagentspro.integrations.alpaca import AlpacaClient, AlpacaConfig, configure_account
from tradingagentspro.modules.execution import BrokerUnavailableError

PATH = "/v2/account/configurations"


def _client(server: FakeAlpaca) -> AlpacaClient:
    config = AlpacaConfig(key_id="K1", secret_key=SecretStr("S1"))
    return AlpacaClient(config, transport=server.transport())


def test_configure_the_account() -> None:
    """spec「设置账户」：只改这两项，其他配置不动。"""
    server = FakeAlpaca()
    before = dict(server.configurations)

    configure_account(_client(server))

    assert server.body("PATCH", PATH) == {"no_shorting": True, "max_margin_multiplier": "1"}
    assert server.configurations == before | {"no_shorting": True, "max_margin_multiplier": "1"}


def test_configuring_again_sends_the_same_request() -> None:
    server = FakeAlpaca()
    client = _client(server)

    configure_account(client)
    configure_account(client)

    first, second = (r.content for r in server.requests)
    assert first == second


def test_cannot_connect_while_configuring() -> None:
    """spec「设置时连不上」。"""
    server = FakeAlpaca()
    server.raise_next("PATCH", PATH, httpx.ConnectError)

    with pytest.raises(BrokerUnavailableError):
        configure_account(_client(server))


def test_client_errors_are_raised_as_is() -> None:
    """设置失败的分类同查询：4xx 原样报错。"""
    server = FakeAlpaca()
    server.respond_next("PATCH", PATH, 403, "forbidden")

    with pytest.raises(httpx.HTTPStatusError):
        configure_account(_client(server))
