"""案件里的交易方案要不要人工确认，见 confirmation-rules spec「案件方案的确认」。"""

from dataclasses import dataclass

from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.notification.config import NotificationConfig


@dataclass(frozen=True, slots=True)
class CaseConfirmation:
    """一个方案的确认规则：要不要请求人工确认，确认超时或请求发不出去以后要不要告警。"""

    required: bool
    alert_on_timeout: bool


def case_plan_confirmation(
    action: TradeAction, *, critic_approved: bool, degraded: bool, config: NotificationConfig
) -> CaseConfirmation:
    """通过风控的案件方案要不要人工确认。

    critic_approved：Critic 审核的结果是通过；有分歧和 Critic 不可用都是 False。degraded 是做决定
    这一刻是否处于降级模式。开仓类一律要确认、超时不告警；退出类只有自动执行打开、Critic 通过、
    不处于降级模式时不要确认，其余要确认、超时告警。
    """
    if action.is_opening:
        return CaseConfirmation(required=True, alert_on_timeout=False)
    automatic = config.auto_execute_model_exits and critic_approved and not degraded
    return CaseConfirmation(required=not automatic, alert_on_timeout=not automatic)
