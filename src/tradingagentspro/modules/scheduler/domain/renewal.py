"""续期上限：账户开得了仓时，关注股连续续期的次数有上限，见 case-types spec「续期上限」。"""

from tradingagentspro.contracts.risk import Headroom
from tradingagentspro.modules.scheduler.config import SchedulerConfig


def can_open(headroom: Headroom) -> bool:
    """账户放得下一笔新开仓：可用资金不低于最小下单额，并且还有持仓名额。

    这只股票已有持仓时没有持仓名额这一项，不占新名额。
    """
    return headroom.available_cash >= headroom.min_order_value and headroom.open_slots != 0


def renewal_capped(renewals: int, headroom: Headroom, config: SchedulerConfig) -> bool:
    """连续续期次数到了上限，并且开得了仓。开不了仓时不算到上限：原因是账户，不是拖延。"""
    return renewals >= config.max_watch_renewals and can_open(headroom)
