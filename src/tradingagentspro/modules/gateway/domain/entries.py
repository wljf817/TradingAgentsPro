"""Agent 网关写进程序日志的三种条目的说明：派发任务、拒绝任务、生成 Result，见 program-log spec
「Agent 网关的动作」和 update-task-cost-storage 的 design D6。

发起者、目标、任务类型、原因和状态写中文名；截止时间写成 UTC，精确到秒。
"""

from datetime import UTC
from typing import Final

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.signal import NoSignal, Signal
from tradingagentspro.contracts.task import (
    Dispatcher,
    Result,
    ResultStatus,
    Task,
    TaskFailure,
    TaskRejection,
    TaskType,
)
from tradingagentspro.modules.gateway.domain.signal import SignalRejection

_DISPATCHERS: Final = {Dispatcher.PM: "PM", Dispatcher.CRITIC: "Critic"}
_TARGETS: Final = {
    SubAgent.NEWS: "News",
    SubAgent.QUANT: "Quant",
    SubAgent.FUNDAMENTALS: "Fundamentals",
}
# task-dispatch spec「任务清单」
_TASK_TYPES: Final = {
    TaskType.EVENT_IMPACT: "事件影响评估",
    TaskType.NEWS_SINCE: "某日期以来的新闻汇总",
    TaskType.PRICE_REACTION: "价格反应",
    TaskType.TREND_LEVELS: "趋势和支撑阻力",
    TaskType.VOLATILITY: "波动情况",
    TaskType.EARNINGS_VS_EXPECTATIONS: "财报对比预期",
    TaskType.GUIDANCE_CHANGE: "指引变化",
    TaskType.VALUATION: "估值位置",
}
# task-dispatch spec「派发的顺序」的原因一栏
_REJECTIONS: Final = {
    TaskRejection.NOT_CASE_STOCK: "股票不是案件的股票",
    TaskRejection.EXPLORATION_TARGET: "寻股只能派给 Quant 和 Fundamentals",
    TaskRejection.ROUNDS_USED_UP: "轮次已用完",
    TaskRejection.PM_TASKS_USED_UP: "任务数已用完",
    TaskRejection.CRITIC_TASKS_USED_UP: "补充研究额度已用完",
}
# 被拒绝的 Result 写在拒绝任务里，不生成这里的说明
_STATUSES: Final = {
    ResultStatus.COMPLETED: "完成",
    ResultStatus.NO_SIGNAL: "无信号",
    ResultStatus.TIMED_OUT: "超时",
    ResultStatus.FAILED: "失败",
}
_FAILURES: Final = {TaskFailure.FORMAT_INVALID: "格式不合法", TaskFailure.CALL_FAILED: "调用失败"}
# 格式不合法的 Result 的说明可能是 signal-format spec「收到输出时的校验」的原因代码，写成中文
_SIGNAL_REJECTIONS: Final[dict[str, str]] = {
    SignalRejection.HORIZON_NOT_OFFERED: "评估窗口不在配置的选项里",
    SignalRejection.DATA_AFTER_NOW: "数据截至时刻晚于现在",
}


def dispatched_text(task: Task) -> str:
    """派发任务的说明：发起者、目标、任务类型、股票、问题、截止时间；PM 派发出去的任务写第几轮。"""
    deadline = f"{task.deadline.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"
    text = f"{_request(task)}；截止时间 {deadline}"
    return text if task.pm_round is None else f"{text}；PM 的第 {task.pm_round} 轮"


def rejected_text(task: Task, reason: TaskRejection) -> str:
    """拒绝任务的说明：被拒绝的原因；发起者、目标、任务类型、股票、问题。"""
    return f"{_REJECTIONS[reason]}；{_request(task)}"


def result_text(result: Result) -> str:
    """生成 Result 的说明：状态；完成写评分、置信度和评估窗口，无信号写原因，失败写原因和说明；
    有模型的写模型和是不是备用模型。
    """
    text = _STATUSES[result.status]
    output = result.output
    if isinstance(output, Signal):
        text += (
            f"：评分 {output.score}，置信度 {output.confidence}，评估窗口 {output.horizon} 个交易日"
        )
    elif isinstance(output, NoSignal):
        text += f"：{output.reason}"
    elif isinstance(result.reason, TaskFailure):
        assert result.detail is not None  # 失败都有说明
        detail = _SIGNAL_REJECTIONS.get(result.detail, result.detail)
        text += f"，{_FAILURES[result.reason]}：{detail}"
    if result.model_use is not None:
        text += f"；模型 {_model(result.model_use)}"
    return text


def _request(task: Task) -> str:
    """发起者派给目标的任务：任务类型、股票和问题。"""
    return (
        f"{_DISPATCHERS[task.dispatcher]} 派给 {_TARGETS[task.target]}："
        f'{_TASK_TYPES[task.task_type]} {task.symbol}，问题"{task.question}"'
    )


def _model(use: ModelUse) -> str:
    """模型写成"厂商/模型名"，备用模型加注。"""
    return f"{use.model}（备用模型）" if use.fallback else str(use.model)
