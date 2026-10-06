"""Alpaca 接口回报的样例：取自 Alpaca 文档的示例响应，测试只覆盖用到的字段。

和真实接口一样，股数、价格和金额都是字符串。
"""

from typing import Any


def order(**fields: Any) -> dict[str, Any]:
    """一笔订单；默认是文档示例里那笔 accepted 的 AAPL 限价买单。"""
    return {
        "asset_class": "us_equity",
        "asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
        "canceled_at": None,
        "client_order_id": "5680c4bc-9ac1-4a12-a44c-df427ba53032",
        "created_at": "2023-12-12T22:31:24.668464435Z",
        "expired_at": None,
        "extended_hours": False,
        "failed_at": None,
        "filled_at": None,
        "filled_avg_price": None,
        "filled_qty": "0",
        "hwm": None,
        "id": "7b08df51-c1ac-453c-99f9-323a5f075f0d",
        "legs": None,
        "limit_price": "150",
        "notional": None,
        "order_class": "",
        "order_type": "limit",
        "qty": "2",
        "replaced_at": None,
        "replaced_by": None,
        "replaces": None,
        "side": "buy",
        "source": None,
        "status": "accepted",
        "stop_price": None,
        "submitted_at": "2023-12-12T22:31:24.577215743Z",
        "subtag": None,
        "symbol": "AAPL",
        "time_in_force": "gtc",
        "trail_percent": None,
        "trail_price": None,
        "type": "limit",
        "updated_at": "2023-12-12T22:31:24.668464435Z",
    } | fields


def account(**fields: Any) -> dict[str, Any]:
    """账户；默认是文档示例里的账户。"""
    return {
        "account_blocked": False,
        "account_number": "PALPACA_123",
        "accrued_fees": "0",
        "admin_configurations": {},
        "balance_asof": "2023-09-27",
        "buying_power": "245432.61",
        "cash": "122086.5",
        "created_at": "2023-01-01T18:20:20.272275Z",
        "crypto_status": "ACTIVE",
        "crypto_tier": 1,
        "currency": "USD",
        "effective_buying_power": "245432.61",
        "equity": "123346.11",
        "id": "1d9eed04-be39-4e01-9b84-a48ac5bbafcf",
        "initial_margin": "629.8",
        "intraday_adjustments": "0",
        "last_equity": "122011.09751111286868",
        "last_maintenance_margin": "480.73",
        "long_market_value": "1259.61",
        "maintenance_margin": "377.88",
        "multiplier": "2",
        "non_marginable_buying_power": "122086.5",
        "options_buying_power": "122716.305",
        "options_trading_level": 2,
        "pending_reg_taf_fees": "0",
        "pending_transfer_in": "0",
        "portfolio_value": "123346.11",
        "position_market_value": "1259.61",
        "regt_buying_power": "245432.61",
        "short_market_value": "0",
        "shorting_enabled": True,
        "sma": "123369.74",
        "status": "ACTIVE",
        "trade_suspended_by_user": False,
        "trading_blocked": False,
        "transfers_blocked": False,
        "user_configurations": None,
    } | fields


def position(**fields: Any) -> dict[str, Any]:
    """一只持仓；默认是文档示例里的 AAPL 5 股。"""
    return {
        "asset_class": "us_equity",
        "asset_id": "904837e3-3b76-47ec-b432-046db621571b",
        "asset_marginable": True,
        "avg_entry_price": "100.0",
        "change_today": "0.0084",
        "cost_basis": "500.0",
        "current_price": "120.0",
        "exchange": "NASDAQ",
        "lastday_price": "119.0",
        "market_value": "600.0",
        "qty": "5",
        "qty_available": "4",
        "side": "long",
        "symbol": "AAPL",
        "unrealized_intraday_pl": "10.0",
        "unrealized_intraday_plpc": "0.0084",
        "unrealized_pl": "100.0",
        "unrealized_plpc": "0.20",
    } | fields


def configurations(**fields: Any) -> dict[str, Any]:
    """账户配置；默认是新开的模拟盘账户：可以做空，最大保证金倍数 4。"""
    return {
        "disable_overnight_trading": False,
        "fractional_trading": True,
        "max_margin_multiplier": "4",
        "max_options_trading_level": 3,
        "no_shorting": False,
        "ptp_no_exception_entry": False,
        "suspend_trade": False,
        "trade_confirm_email": "all",
    } | fields
