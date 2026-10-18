"""程序日志的条目：行为者、动作和每条记下的字段，见 program-log spec「程序日志的条目」。

行为者和动作存英文代码，说明是中文。以后的变更加别的行为者和动作，见 add-program-log 的 design D1。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class Actor(StrEnum):
    """行为者，见 program-log spec。"""

    RISK = "risk"  # 风控引擎
    EXECUTION = "execution"  # 执行模块
    HUMAN = "human"  # 人
    SYSTEM = "system"  # 系统
    STOCK_POOL = "stock_pool"  # 股票池
    SCHEDULER = "scheduler"  # 调度器
    GATEWAY = "gateway"  # Agent 网关


class Action(StrEnum):
    """动作，见 program-log spec 各个行为者的动作表。"""

    RISK_CHECK = "risk_check"  # 风控检查
    RECHECK = "recheck"  # 下单前再校验
    RELEASE = "release"  # 释放预留
    SUBMIT = "submit"  # 提交订单
    ORDER_CHANGE = "order_change"  # 订单变化
    CANCEL = "cancel"  # 撤单
    STOP_CHANGE = "stop_change"  # 止损单变化
    UNPROTECTED = "unprotected"  # 无保护仓位
    PROTECTED = "protected"  # 无保护解除
    RECONCILE = "reconcile"  # 对账
    WAIT_OPEN = "wait_open"  # 等开盘
    ENTER_PLAN = "enter_plan"  # 录入方案
    CONFIRM = "confirm"  # 确认
    REJECT = "reject"  # 拒绝
    TIMEOUT = "timeout"  # 超时
    STARTUP = "startup"  # 启动
    SHUTDOWN = "shutdown"  # 停止
    CRASH_RECOVERY = "crash_recovery"  # 崩溃恢复
    ENTER_DEGRADED = "enter_degraded"  # 进入降级模式
    DEGRADED_UPDATE = "degraded_update"  # 降级模式里的变化
    LEAVE_DEGRADED = "leave_degraded"  # 退出降级模式
    BREAKER = "breaker"  # 熔断
    EMERGENCY_EXIT = "emergency_exit"  # 紧急风险退出
    ALERT = "alert"  # 告警
    REQUEST_CONFIRMATION = "request_confirmation"  # 请求确认
    FINISH_CONFIRMATION = "finish_confirmation"  # 结束确认
    POOL_ADD = "pool_add"  # 加入股票池
    POOL_REMOVE = "pool_remove"  # 删除
    POOL_PROMOTE = "pool_promote"  # 升级
    POOL_DEMOTE = "pool_demote"  # 降为候选
    POOL_KEEP = "pool_keep"  # 保持
    POOL_SCORE = "pool_score"  # 记录评分
    POOL_THESIS = "pool_thesis"  # 论点更新
    POOL_EXPIRE = "pool_expire"  # 超期移出
    POOL_BOUGHT = "pool_bought"  # 买入成交
    POOL_CLOSED = "pool_closed"  # 清仓
    APPROVE_FOLLOW_UP = "approve_follow_up"  # 批准跟进
    REJECT_FOLLOW_UP = "reject_follow_up"  # 拒绝跟进
    REPLACE_FOLLOW_UP = "replace_follow_up"  # 替换跟进
    VOID_FOLLOW_UP = "void_follow_up"  # 作废跟进
    TRIGGER_FOLLOW_UP = "trigger_follow_up"  # 触发跟进
    DISPATCH_TASK = "dispatch_task"  # 派发任务
    REJECT_TASK = "reject_task"  # 拒绝任务
    TASK_RESULT = "task_result"  # 生成 Result
    TAKE_SNAPSHOT = "take_snapshot"  # 生成快照


ACCOUNT = "account"  # 对象：整个券商账户
WHOLE_SYSTEM = "system"  # 对象：整个程序


@dataclass(frozen=True, slots=True)
class LogEntry:
    """程序日志的一条。

    - recorded_at：写入时从时钟读取的时刻
    - subject：对象，动作针对的东西的标识
    - inputs、outputs：输入和输出的引用
    - case_id、duration、tokens、cost：案件 ID、耗时、token 和费用，不适用时为 None
    """

    recorded_at: datetime
    actor: Actor
    action: Action
    subject: str
    detail: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    case_id: UUID | None = None
    duration: timedelta | None = None
    tokens: int | None = None
    cost: Decimal | None = None
