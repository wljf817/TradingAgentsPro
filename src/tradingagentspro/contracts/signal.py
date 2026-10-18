"""信号和无信号：子 agent 的输出是两者之一，PM 的结论里对一只股票的信号也是同样的格式。

信号来自模型输出，所以在这里校验格式，见 signal-format spec「信号的格式」「评分的刻度」「无信号」。
评估窗口是否在配置的选项里、数据截至时刻是否晚于现在，由 Agent 网关在收到输出时检查（spec
「收到输出时的校验」）：已经写入的信号读回时不再查这两项。
"""

from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# 状态库条目的 ID：快照里的条目见 contracts.snapshot 的 entry_id
type EntryId = Annotated[str, Field(min_length=1)]


class Signal(BaseModel):
    """一个信号：子 agent 的输出，或者 PM 结论里对一只股票的信号。

    信号里不写股票：子 agent 的信号针对 Task 指定的股票，PM 的信号针对案件的股票或者寻股加入的
    那只候选。构造以后不能修改，只按写入的评估窗口评估。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    horizon: int = Field(ge=1)  # 评估窗口，交易日数；只用于评估准确性，不代表持仓周期
    score: int = Field(ge=-2, le=2)  # 评分，含义见 spec「评分的刻度」
    confidence: Decimal = Field(ge=0, le=1)  # 置信度：这个判断成立的把握
    data_as_of: AwareDatetime  # 数据截至时刻：所依据的数据最新到什么时候
    cited_entries: tuple[EntryId, ...] = Field(min_length=1)  # 引用的状态库条目
    rationale: str = Field(min_length=1)  # 理由
    risks: str = Field(min_length=1)  # 主要风险：可能让这个判断落空的风险


class NoSignal(BaseModel):
    """无信号：根据提供的数据给不出判断。不写信号的其他项。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1)  # 为什么给不出判断，比如数据不足
    cited_entries: tuple[EntryId, ...] = ()  # 看过的状态库条目，可以为空


# 子 agent 的输出。两种的必填字段不重叠，加上 extra="forbid"，联合类型分得清
type SubAgentOutput = Signal | NoSignal
