"""事件：事件检测按代码规则检测出的一个事件，以及它的类别、来源和处理。

事件案件按类别决定至少派给哪个子 agent，见 case-types spec「事件要求的子 agent」。事件的内容见
event-rules spec「事件的内容」和 add-event-detection 的 design D2：事件模块产出它、存进事件表，
快照和案件上下文原样复制它，所以放在 contracts。
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict


class EventCategory(StrEnum):
    """事件的类别。止损单成交不是事件，开的是卖出复盘。"""

    PRICE_VOLUME = "price_volume"  # 价格和成交量异动
    NEWS = "news"  # 新闻
    FILING = "filing"  # 公告
    EARNINGS = "earnings"  # 财报发布
    ENTRY = "entry"  # 关注股的入场条件满足
    THESIS_CONDITION = "thesis_condition"  # 持仓达到目标价或证伪条件触发


class EventSource(StrEnum):
    """事件从哪里检测出来。用抽象的名字：数据源是哪一家只在启动时决定。"""

    QUOTES = "quotes"  # 行情
    NEWS = "news"  # 新闻源
    FILINGS = "filings"  # 公告源
    EARNINGS_CALENDAR = "earnings_calendar"  # 财报日历


class EventOutcome(StrEnum):
    """事件怎样处理。"""

    QUEUED = "queued"  # 排队
    EMERGENCY_EXIT = "emergency_exit"  # 交给紧急风险退出，不排队


class Event(BaseModel):
    """一个事件，见 event-rules spec「事件的内容」。

    - event_id：`<股票>:<交易日>:<种类>`，同时是去重的键，见 spec「事件 ID 和去重」
    - holding_risk：持仓风险级别，只有持仓的事件可以是
    - strength：价格异动是涨跌幅（下跌为负，4 位小数）；成交量异动是量比；新闻和公告是级别 1 或 2；
      财报发布没有
    - occurred_at：行情的更新时刻、新闻的发布时刻、公告的受理时刻或者财报日历的预计发布时刻
    - detected_at：检测出这个事件的那一轮开始的时刻
    - summary：中文说明
    - refs：新闻 ID、accession number 或财报发布的键；价格和成交量没有
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    symbol: str
    category: EventCategory
    holding_risk: bool
    source: EventSource
    strength: Decimal | None
    occurred_at: AwareDatetime
    detected_at: AwareDatetime
    summary: str
    refs: tuple[str, ...]
    outcome: EventOutcome
