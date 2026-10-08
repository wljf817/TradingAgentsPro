"""事件的类别：事件案件按它决定至少派给哪个子 agent，见 case-types spec「事件要求的子 agent」。

事件的完整格式（阈值、强度、到价提醒）由 M4 的事件检测定。
"""

from enum import StrEnum


class EventCategory(StrEnum):
    """事件的类别，由 M4 的事件检测标注。止损单成交不是事件，开的是卖出复盘。"""

    PRICE_VOLUME = "price_volume"  # 价格和成交量异动
    NEWS = "news"  # 新闻
    FILING = "filing"  # 公告
    EARNINGS = "earnings"  # 财报发布
    ENTRY = "entry"  # 关注股的入场条件满足
    THESIS_CONDITION = "thesis_condition"  # 持仓达到目标价或证伪条件触发
