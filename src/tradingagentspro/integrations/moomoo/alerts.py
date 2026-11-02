"""moomoo 到价提醒：用 OpenD 的到价提醒实现事件模块的 PriceAlerts。

见 moomoo-price-alerts spec 和 add-thesis-conditions-and-price-alerts 的 design D7、D8。备注是 TAPro
的提醒是系统设的，只增删这些；值按 OpenD 存的精度取整以后再比较。推送的处理登记在 OpenD 行情连接
上，在 SDK 的回调线程里只调用登记的回调，不看推送的内容。
"""

from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final, Protocol

from moomoo import (
    RET_OK,
    Market,
    PriceReminderFreq,
    PriceReminderHandlerBase,
    PriceReminderMarketStatus,
    PriceReminderType,
    SetPriceReminderOp,
)

from tradingagentspro.modules.events import (
    AlertChanges,
    AlertKind,
    PriceAlert,
    PriceAlertsUnavailableError,
)

# 系统设的到价提醒的备注：SDK 注释写最多 10 个字符
NOTE: Final = "TAPro"

_US_PREFIX = "US."
_TYPES: Final = {
    AlertKind.PRICE_UP: PriceReminderType.PRICE_UP,
    AlertKind.PRICE_DOWN: PriceReminderType.PRICE_DOWN,
    AlertKind.CHANGE_UP: PriceReminderType.CHANGE_RATE_UP,
    AlertKind.CHANGE_DOWN: PriceReminderType.CHANGE_RATE_DOWN,
    AlertKind.VOLUME_UP: PriceReminderType.VOLUME_UP,
}
_KINDS: Final = {reminder_type: kind for kind, reminder_type in _TYPES.items()}
_CHANGES: Final = frozenset({AlertKind.CHANGE_UP, AlertKind.CHANGE_DOWN})
_PLACES: Final = Decimal("0.001")  # OpenD 保留价格和百分比的 3 位小数，多的舍去
_VOLUME_STEP: Final = 10  # 美股成交量按 10 股向下取整

# OpenD 上的一个提醒：代码、类型、OpenD 单位的值（已经取整）
type _Setting = tuple[str, str, Decimal]


class ReminderContext(Protocol):
    """适配器用到的 OpenD 行情连接的那部分：moomoo 的 OpenQuoteContext 满足它。

    返回 (RET_OK, 结果)；失败时返回 (其他值, 错误信息)。
    """

    def get_price_reminder(
        self, code: str | None = None, market: str | None = None
    ) -> tuple[int, Any]: ...

    def set_price_reminder(
        self,
        code: str,
        op: str,
        key: int | None = None,
        reminder_type: str | None = None,
        reminder_freq: str | None = None,
        value: float | None = None,
        note: str | None = None,
        reminder_session_list: list[str] | None = None,
    ) -> tuple[int, Any]: ...

    def set_handler(self, handler: object) -> int: ...


class MoomooPriceAlerts:
    """满足事件模块的 PriceAlerts：每次同步读一次美股的到价提醒列表，不重试。"""

    def __init__(self, context: ReminderContext) -> None:
        self._context = context

    def sync(self, wanted: frozenset[PriceAlert]) -> AlertChanges:
        """读系统设的提醒，先删多的、再设少的；一次调用失败记进结果，其余照做。"""
        ret, data = self._context.get_price_reminder(market=Market.US)
        if ret != RET_OK:
            raise PriceAlertsUnavailableError(f"OpenD 到价提醒：{data}")
        current: dict[_Setting, int] = {}
        for row in data.to_dict("records"):
            if row["note"] == NOTE:
                value = Decimal(repr(float(row["value"])))
                current[_rounded(row["code"], row["reminder_type"], value)] = int(row["key"])
        settings = {_setting(alert): alert for alert in wanted}
        failed: list[tuple[PriceAlert, str]] = []
        removed = added = 0
        for setting in sorted(current.keys() - settings.keys()):
            code = setting[0]
            ret, data = self._context.set_price_reminder(
                code, SetPriceReminderOp.DEL, key=current[setting]
            )
            if ret == RET_OK:
                removed += 1
            else:
                failed.append((_alert(setting), str(data)))
        for setting in sorted(settings.keys() - current.keys()):
            code, reminder_type, value = setting
            ret, data = self._context.set_price_reminder(
                code,
                SetPriceReminderOp.ADD,
                reminder_type=reminder_type,
                reminder_freq=PriceReminderFreq.ONCE_A_DAY,
                value=float(value),
                note=NOTE,
                reminder_session_list=[PriceReminderMarketStatus.OPEN],
            )
            if ret == RET_OK:
                added += 1
            else:
                failed.append((settings[setting], str(data)))
        return AlertChanges(added, removed, tuple(failed))

    def listen(self, on_push: Callable[[], None]) -> None:
        """在 OpenD 行情连接上登记推送的处理；SDK 重连以后照样有效。"""
        self._context.set_handler(_PushHandler(on_push))


class _PushHandler(PriceReminderHandlerBase):  # type: ignore[misc]  # SDK 没有类型标注
    """到价提醒推送的处理：在 SDK 的回调线程里调用 on_push，不解析推送的内容。"""

    def __init__(self, on_push: Callable[[], None]) -> None:
        super().__init__()
        self._on_push = on_push

    def on_recv_rsp(self, rsp_pb: object) -> tuple[int, None]:
        self._on_push()
        return RET_OK, None


def _setting(alert: PriceAlert) -> _Setting:
    """到价提醒在 OpenD 上的样子：涨跌幅换成百分比，再按 OpenD 的精度取整。"""
    value = alert.value * 100 if alert.kind in _CHANGES else alert.value
    return _rounded(_US_PREFIX + alert.symbol, _TYPES[alert.kind], value)


def _rounded(code: str, reminder_type: str, value: Decimal) -> _Setting:
    """按 OpenD 存的精度取整：成交量按 10 股向下取整，其余保留 3 位小数、多的舍去。"""
    if reminder_type == PriceReminderType.VOLUME_UP:
        value = value // _VOLUME_STEP * _VOLUME_STEP
    else:
        value = value.quantize(_PLACES, ROUND_DOWN)
    return code, reminder_type, value


def _alert(setting: _Setting) -> PriceAlert:
    """OpenD 上的一个提醒换回到价提醒，用来报告删不掉的提醒。"""
    code, reminder_type, value = setting
    kind = _KINDS[reminder_type]
    return PriceAlert(
        code.removeprefix(_US_PREFIX), kind, value / 100 if kind in _CHANGES else value
    )
