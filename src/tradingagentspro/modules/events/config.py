"""事件规则的阈值和默认值，含义见 event-rules spec「事件规则的配置」「新闻关键词」
「公告的初筛」。
"""

from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# 去掉首尾空白以后不为空的文字
Word = Annotated[str, Field(min_length=1)]


class KeywordRule(BaseModel):
    """一个新闻关键词：级别 1 一般、2 重大，以及是否负面。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    keyword: Word
    level: Literal[1, 2]
    negative: bool


def _rules(level: Literal[1, 2], negative: bool, *keywords: str) -> tuple[KeywordRule, ...]:
    return tuple(KeywordRule(keyword=k, level=level, negative=negative) for k in keywords)


# 「新闻关键词」的默认表，一行一组
DEFAULT_KEYWORDS: Final = (
    *_rules(2, False, "acqui", "merger", "takeover", "buyout", "tender offer"),
    *_rules(2, False, "go private", "take private"),
    *_rules(2, False, "FDA", "approval"),
    *_rules(2, True, "complete response letter", "clinical hold"),
    *_rules(2, False, "guidance", "outlook"),
    *_rules(2, True, "lowers guidance", "cuts guidance", "withdraws guidance"),
    *_rules(2, True, "lowers outlook", "cuts outlook"),
    *_rules(2, True, "offering", "dilut"),
    *_rules(2, True, "lawsuit", "sues", "sued", "investigation", "probe", "subpoena"),
    *_rules(2, True, "class action", "fraud"),
    *_rules(2, True, "bankrupt", "chapter 11", "restate", "delist", "halt"),
    *_rules(2, True, "resign", "steps down", "step down", "departure"),
    *_rules(2, True, "short seller", "short report"),
    *_rules(1, False, "upgrade"),
    *_rules(1, True, "downgrade"),
    *_rules(1, False, "buyback", "repurchase", "dividend"),
    *_rules(1, True, "recall", "suspends dividend", "cuts dividend"),
)


class FilingTiers(BaseModel):
    """「公告的初筛」的表：表单按 SEC 的写法完全相同才算，8-K 另看 item。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    current_reports: tuple[Word, ...] = ("8-K", "8-K/A")  # 看 item 的表单
    earnings_items: tuple[Word, ...] = ("2.02",)  # 财报发布
    negative_items: tuple[Word, ...] = ("1.03", "2.06", "3.01", "4.02")  # 重大，负面
    major_items: tuple[Word, ...] = ("1.01", "2.01", "5.01", "5.02")  # 重大
    offerings: tuple[Word, ...] = ("S-1", "S-3", "424B4", "424B5", "424B7")  # 增发：重大，负面
    major_forms: tuple[Word, ...] = ("SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A")
    ordinary_forms: tuple[Word, ...] = ("10-Q", "10-K")  # 一般


class EventRulesConfig(BaseModel):
    """事件规则的配置。只要求各项自身合法，不校验配置项之间的关系。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    # 持仓上涨、关注上涨和下跌的涨跌幅
    price_threshold: Decimal = Field(default=Decimal("0.05"), gt=0, lt=1)
    # 候选上涨和下跌的涨跌幅
    candidate_price_threshold: Decimal = Field(default=Decimal("0.08"), gt=0, lt=1)
    # 持仓跌幅达到紧急阈值乘这个比例时是持仓风险级别的价格下跌事件
    holding_risk_ratio: Decimal = Field(default=Decimal("0.5"), gt=0, lt=1)
    volume_ratio: Decimal = Field(default=Decimal(3), gt=0)  # 持仓和关注的量比阈值
    candidate_volume_ratio: Decimal = Field(default=Decimal(5), gt=0)  # 候选的量比阈值
    # 开盘以后过了这么久才判断成交量
    volume_delay: timedelta = Field(default=timedelta(minutes=30), gt=timedelta(0))
    news_sources: tuple[Word, ...] = Field(default=("benzinga",), min_length=1)  # 认的稿件来源
    max_symbols: int = Field(default=2, gt=0)  # 新闻最多标注的股票数
    keywords: tuple[KeywordRule, ...] = Field(default=DEFAULT_KEYWORDS, min_length=1)
    exclusions: tuple[Word, ...] = ("maintains", "reiterates", "reaffirms")
    filing_tiers: FilingTiers = FilingTiers()
    # 同一只股票这么多个交易日（含今天）里最多一个财报发布事件
    earnings_sessions: int = Field(default=5, gt=0)
    # 盘后和没写发布时段的财报，发布日收盘以后等这么久
    after_close_wait: timedelta = Field(default=timedelta(minutes=60), gt=timedelta(0))
