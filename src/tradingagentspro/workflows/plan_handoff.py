"""交给下单的接口：案件工作流把方案交给下单那一侧，下单那一侧把结果送回案件。

见 case-workflow spec「交给下单」和 add-case-workflow 的 design D4。案件工作流和下单流程都 import
这里，下单流程不 import 案件工作流。下单那一侧由「方案接下单」实现：PlanHandoff 在案件工作流的工作流
函数里被调用（它要启动下单流程，DBOS 不允许在 step 里启动工作流），之后的结果由下单流程的工作流函数
用 report_plan 送回。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from dbos import DBOS

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.scheduler import CriticOutcome

PLAN_TOPIC = "plan"  # 下单那一侧送回结果的消息主题


class PlanEvent(StrEnum):
    """下单那一侧送回案件的结果，见 case-workflow spec「交给下单」。"""

    CONFIRMED = "confirmed"  # 已确认
    DECLINED = "declined"  # 已拒绝
    TIMED_OUT = "timed_out"  # 已超时，包括确认请求发不出去
    SUBMITTED = "submitted"  # 订单已提交
    RECHECK_VOIDED = "recheck_voided"  # 再校验作废
    FAILED = "failed"  # 出错


@dataclass(frozen=True, slots=True)
class Handoff:
    """交接给出的结果，见 case-workflow spec「交给下单」。"""

    decision: RiskDecision
    confirming: bool  # 通过或调整以后要人工确认；风控拒绝时为 False


class PlanHandoff(Protocol):
    """下单那一侧，由「方案接下单」实现。"""

    def submit(self, case_id: UUID, plan: TradePlan, critic: CriticOutcome) -> Handoff | None:
        """在工作流函数里调用：检查并预留，通过或调整时启动下单流程；读不到券商账户时为 None。"""
        ...


def report_plan(case_id: UUID, event: PlanEvent) -> None:
    """把一个结果送给案件 case_id，在下单流程的工作流函数里调用。"""
    DBOS.send(str(case_id), event.value, topic=PLAN_TOPIC)
