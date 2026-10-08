"""论点：PM 把股票升级为关注时写的关注论点、新建仓时写的投资论点，以及其中的催化剂和代码能
检测的条件。

论点来自模型输出，所以在这里校验格式，见 stock-pool spec「关注论点的格式」和 case-types spec
「投资论点的格式」。复查日和催化剂截止日期取什么值，由「复查日与催化剂」校验。
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConditionKind(StrEnum):
    """代码能检测的条件比较哪个量。技术指标类由 M4 的事件检测再加。"""

    PRICE = "price"  # 最新价
    CHANGE = "change"  # 当日涨跌幅：相对前一交易日收盘价的比例，下跌为负
    VOLUME = "volume"  # 当日累计成交量（股）


class Comparison(StrEnum):
    AT_LEAST = "at_least"  # 不低于
    AT_MOST = "at_most"  # 不高于


class Condition(BaseModel):
    """代码能检测的条件。价格和成交量的值大于 0，成交量只能是不低于。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ConditionKind
    comparison: Comparison
    value: Decimal

    @model_validator(mode="after")
    def _check_value_for_kind(self) -> Self:
        if self.kind is not ConditionKind.CHANGE and self.value <= 0:
            raise ValueError("价格和成交量的值必须大于 0")
        if self.kind is ConditionKind.VOLUME and self.comparison is not Comparison.AT_LEAST:
            raise ValueError("成交量只能是不低于")
        return self


class ReferenceKind(StrEnum):
    SCHEDULE = "schedule"  # 日程项
    EVENT = "event"  # 事件


class CatalystReference(BaseModel):
    """催化剂引用的日程项或事件。两者都在 M4 才有，ID 现在不限定格式。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    kind: ReferenceKind
    ref_id: str = Field(min_length=1)


class Catalyst(BaseModel):
    """催化剂和它的截止日期。一份论点只有一个，写最近的那一个。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    description: str = Field(min_length=1)
    deadline: date
    # 没有固定日期的催化剂可以先不引用，事件发生后再关联
    reference: CatalystReference | None = None


class WatchThesis(BaseModel):
    """关注论点。复查日和截止日期取什么值由「复查日与催化剂」校验。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1)  # 看好理由
    # 任一条件满足就算入场条件满足
    entry_conditions: tuple[Condition, ...] = Field(min_length=1)
    catalyst: Catalyst
    invalidation: str = Field(min_length=1)  # 证伪条件
    review_day: date  # 复查日


class InvestmentThesis(BaseModel):
    """投资论点：新建仓时附上；持仓的论点更新也是一份完整的投资论点，整份代替原来的。

    目标价单独一个字段，不写成条件：它同时是收益目标，M4 设到价提醒时由代码换成条件。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1)  # 买入理由
    catalyst: Catalyst
    target_price: Decimal = Field(gt=0)
    invalidation: str = Field(min_length=1)  # 证伪条件
    # 证伪条件中代码能检测的部分，任一满足就算触发；很多证伪条件不是价格类，可以没有
    invalidation_conditions: tuple[Condition, ...] = ()
    review_day: date  # 复查日
