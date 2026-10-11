# Design

## Context

现状（`add-emergency-exit-flow`、`add-order-flow` 之后）：

- 通知模块（`modules/notification`）是架构图的「人工确认」：`NotificationConfig` 只有确认超时；`NotificationService.request_confirmation(plan, decision)` 要一个风控结果，被拒的方案抛 `ValueError`；`Notifier` 端口有 `alert`、`request_confirmation`、`finish_confirmation` 三个方法
- Discord 适配器：确认消息的按钮 `custom_id` 里带方案 ID、选择和截止时刻，重启以后照样能用；同一个方案 ID 重复请求返回第一次那条（`_requests` 按方案 ID 去重）；按钮的结果交给一个 `DecisionHandler(plan_id, outcome)`，bootstrap 现在传的是 `order_workflow.deliver_decision`：这个方案的下单流程不在等确认时抛错，Discord 请人再按一次
- 紧急退出流程（`workflows/emergency_flow.py`）在巡检线程里运行，状态全在内存：`_records` 按清仓方案 ID 记当天开始的紧急退出（`add-emergency-exit-flow` 的 design D2）。`judge` 不拿锁，`advance` 拿一次锁做作废、撤单、风控检查、再校验和提交，`report` 在盘外也做。清仓方案每只股票每天一个（uuid5），本地备用止损用同一个方案
- 风控按方案 ID 记预留：有过预留的方案再检查时返回第一次的结果，释放以后再校验给出"方案已结束"（risk-reservations spec「同一方案重复检查」、pre-order-recheck spec）。所以清仓方案一旦预留又释放，当天就不能再用
- Critic 审核的结果 `CriticOutcome` 在 `modules/scheduler/domain/stage.py`，模块之间不能互相 import

动机见 proposal.md 的 Why，要求见 specs/。

### 模块边界

```
modules/notification
  config.py               NotificationConfig 加 auto_execute_model_exits、confirm_emergency_exits
  domain/rules.py         新：CaseConfirmation、case_plan_confirmation（案件方案的确认规则）
  domain/confirmation.py  加 EmergencyConfirmationRequest
  ports.py                Notifier 加 request_emergency_confirmation
  service.py              加 request_emergency_confirmation
  __init__.py             导出新名字
integrations/discord
  messages.py             加 emergency_confirmation_content；confirmation_view 改为按方案 ID 和截止时刻建按钮
  notifier.py             实现 request_emergency_confirmation，和 request_confirmation 共用去重和发送
workflows/emergency_flow.py
                          EmergencyDecisions（按钮结果的登记）；_Record 加确认状态；judge、advance、report
                          按确认状态处理；新的告警文字
bootstrap/app.py          一份 NotificationConfig 给通知和紧急退出流程；按钮结果的分发
```

跨模块传递的 Pydantic 模型只有已有的 `TradePlan`（紧急退出流程交给通知模块）；规则的输入用已有的 `TradeAction`。不新增、不修改 contracts。

## Goals / Non-Goals

**Goals:**

- 两个开关关闭时，程序的行为和现在完全一样
- 紧急退出在等确认期间不改动订单、方案、预留和止损单
- 案件方案的确认规则是一个纯函数，M4 直接调用，不用再改通知模块

**Non-Goals:**

- 紧急退出的确认不跨重启保存（见 D4）
- 下单流程不改：`/plan` 的方案和现在一样请求确认

按「编程风格」省略的情况：

- 等确认时重启，重启前后有两条确认消息：按了其中一条以后，另一条的按钮按下时没有送达（回调抛错），到截止时刻以后按下显示已超时，不去掉按钮
- 收盘前最后一轮以后才送到的确认：按到收盘还在等确认处理，确认消息改为已作废
- 确认消息里的当前持仓是请求时的股数，之后的成交不更新消息
- 等确认期间止损单成交、持仓变为 0：超时告警照样写"止损单照挂"；确认了的按现有规则得到没有可卖的股数并告警
- 等确认期间收到同一只股票的别的交易方案：照常走下单流程；确认以后由「一只股票的清仓」作废或撤掉

## Decisions

### D1 规则放在通知模块，Critic 的结果用一个布尔值传入

`modules/notification/domain/rules.py`：

```python
@dataclass(frozen=True, slots=True)
class CaseConfirmation:
    required: bool  # 要人工确认
    alert_on_timeout: bool  # 确认超时或发不出去以后告警


def case_plan_confirmation(
    action: TradeAction, *, critic_approved: bool, degraded: bool, config: NotificationConfig
) -> CaseConfirmation: ...
```

按 confirmation-rules spec「案件方案的确认」的表：开仓类要确认、不告警；退出类在 `config.auto_execute_model_exits`、`critic_approved`、不降级三者都满足时不要确认，否则要确认、告警。

两个开关都放在 `NotificationConfig`，和确认超时在一起：哪些方案要确认、等多久，都是「人工确认」这个节点的规则。紧急退出流程读同一份配置（D7）。

Critic 的结果用 `critic_approved: bool`：规则只区分「通过」和「其他」，M4 的案件工作流传 `state.critic is CriticOutcome.APPROVED`。备选是把 `CriticOutcome` 移到 contracts，要改调度器模块，本变更只改「人工确认」节点，不这样做。

### D2 紧急退出用单独的确认请求，等确认期间不碰风控

`domain/confirmation.py` 加：

```python
@dataclass(frozen=True, slots=True)
class EmergencyConfirmationRequest:
    plan: TradePlan  # 代码触发的清仓方案
    quantity: int  # 当前持仓股数
    reason: str  # 原因，紧急退出流程写好的文字
    deadline: datetime
```

`Notifier.request_emergency_confirmation(request) -> PendingConfirmation`，`NotificationService.request_emergency_confirmation(plan, quantity, reason)` 按确认超时算截止时刻。结果、按钮、`finish_confirmation` 和交易方案的确认消息共用。

不复用 `request_confirmation`：它要一个风控结果，就得在请求确认之前检查清仓方案并预留。那样会有三个问题：

- 人拒绝或超时以后要释放预留，而释放过的方案当天不能再用（Context），本地备用止损就没法越过确认清仓
- 等确认时重启，内存里的记录丢了，股票不再触发时这份卖出预留没人释放，一直挡着这只股票别的卖出方案
- 风控给出的股数扣掉了别的卖出方案的预留，消息里的股数会比确认以后实际卖出的少

所以等确认期间不检查风控、不预留；确认以后照旧在「一只股票的清仓」第 4 步检查、再校验、提交。

Discord 适配器：`emergency_confirmation_content(request, user_id)` 按 discord-notification spec「紧急退出的确认消息」的表写正文；`confirmation_view(plan_id, deadline)` 两种消息共用；`_request` 和 `_send_confirmation` 改为接收方案 ID、截止时刻、正文和按钮，两种请求按方案 ID 共用 `_requests` 去重（交易方案的 ID 是随机 UUID，清仓方案的是 uuid5，不会冲突）。

### D3 每只股票的确认状态记在 `_Record` 里

```python
class Gate(StrEnum):
    OPEN = "open"  # 不用确认，或者已经确认：按「一只股票的清仓」处理
    ASKING = "asking"  # 要请求确认
    WAITING = "waiting"  # 已经请求，在等结果
    DECLINED = "declined"  # 拒绝、超时或者发不出去：当天不再处理（本地备用止损除外）
```

`_Record` 加 `gate`、`message_id`、`deadline`。

- `judge`：新记录的状态是 OPEN（开关关闭，或者这一轮本地备用止损触发）或 ASKING。本地备用止损改为对每只持仓都判断（现在只判断没有触发紧急退出的）：已有记录在 WAITING 或 DECLINED、这一轮备用止损触发时，WAITING 的确认消息报告已作废（锁外），记录改为 OPEN，触发的原因换成备用止损，`announced` 改回 False，这样开始的告警写的是备用止损。新记录的原因仍按组合熔断、个股跌幅、备用止损的顺序取第一个
- `_pending`：DECLINED 的记录不在里面
- `advance`：
  - ASKING：锁外请求确认，先在 `EmergencyDecisions` 登记（D4）再发送，免得很快按下的按钮送不到；成功改 WAITING，发不出去改 DECLINED，写 ERROR，告警
  - WAITING：从登记里取结果。确认：改 OPEN，这一轮接着走现有的步骤；拒绝：改 DECLINED，写 INFO；没有结果并且不早于截止时刻：关闭登记（关闭时刚好送到的结果照样采用），报告已超时，改 DECLINED，告警
  - OPEN：现有的步骤不变
- `report`：盘外时 WAITING 的记录关闭登记、报告已作废、告警到收盘还没有确认，标记结束；OPEN 的照旧（没有清仓单就释放预留、告警）

开关关闭时所有记录都是 OPEN，`advance` 和 `report` 走的就是现在的代码。

### D4 按钮结果用一个线程安全的登记交给紧急退出流程，不跨重启

`EmergencyDecisions`（`workflows/emergency_flow.py`）：一把锁加一个按方案 ID 的字典，四个方法：

- `expect(plan_id)`：开始等这个方案的结果
- `deliver(plan_id, outcome) -> bool`：在等、还没有结果时记下并返回 True；否则返回 False
- `take(plan_id) -> ConfirmationOutcome | None`：有结果就取走并停止等；没有就返回 None，继续等
- `close(plan_id) -> ConfirmationOutcome | None`：停止等，返回已经送到的结果

bootstrap 先建登记，再建 Discord 通知通道，回调是：

```python
def on_decision(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
    if not decisions.deliver(plan_id, outcome):
        deliver_decision(plan_id, outcome)
```

不在等的方案交给下单流程，下单流程不认识时照旧抛错，Discord 请人再按一次。登记由 bootstrap 建好分别交给回调和紧急退出流程，解开「通知通道要回调、紧急退出流程要通知服务」的先后顺序。

备选是像下单流程一样，每个紧急退出的确认用一个 DBOS 工作流等结果，跨重启保存。紧急退出流程是按每一轮的价格判定的内存状态（`add-emergency-exit-flow` 的 design D2），重启以后本来就重新判定；为一个默认关闭的开关做持久化不值得。重启的处理见 emergency-exit-flow spec「重启以后」。

### D5 只在盘中处理确认的结果和超时

`judge` 在盘外返回 None，`advance` 不被调用，所以确认的结果和超时只在盘中处理；收盘以后第一轮由 `report` 收尾（D3）。截止时刻晚于收盘的确认，到收盘时作废。

### D6 告警文字

- 请求确认的原因用 `Trigger.detail`，和开始紧急退出的告警相同
- 新增：确认超时、确认请求发不出去、到收盘还没有确认三段告警文字，写明股票、没有执行、今天不再紧急退出、止损单照挂
- `breaker_text` 加一个参数：开关打开时写"每只持仓的清仓要确认"
- 请求确认、收到拒绝、确认消息作废写 INFO；开始紧急退出的告警照旧在 OPEN 以后第一次 `advance` 时推送

### D7 紧急退出流程拿到通知配置

`EmergencyFlow` 的构造参数加 `NotificationConfig` 和 `EmergencyDecisions`，只读 `confirm_emergency_exits`；截止时刻由 `NotificationService` 算。bootstrap 建一份 `NotificationConfig()`，交给 `NotificationService` 和 `EmergencyFlow`。

### D8 测试

- `tests/modules/notification/domain/test_rules.py`：confirmation-rules spec 的每个场景，开关从配置读
- `tests/modules/notification/test_service.py`：请求紧急退出的确认的截止时刻、发不出去
- `tests/integrations/discord/`：紧急退出确认消息的正文；重复请求只发一条；按钮交给回调，截止以后按显示已超时，报告作废
- `tests/fakes/notifier.py`：`FakeNotifier` 加 `emergency_requests`，和 `requests` 一样记录、共用 `fail_next`
- `tests/workflows/`：emergency-exit-flow spec 新增和修改的场景；测试用 `EmergencyDecisions.deliver` 模拟按钮；`World` 加打开开关的参数。已有场景用默认配置，不改
- `tests/bootstrap/`：按钮结果的分发

## Risks / Trade-offs

- [开关打开时 Discord 断线] → 当天不紧急退出这些股票，只靠止损单。这是完整版的规则（确认请求发不出去按超时处理）；开关默认关
- [人不在] → 30 分钟以后超时，不执行，止损单照挂，告警
- [确认状态在内存] → 重启以后仍然触发的再请求一次（emergency-exit-flow spec「重启以后」）；已经确认、在等撤单时重启，要再确认一次

## Migration Plan

没有新表和迁移；两个开关默认关，按 `deploy/README.md`「更新」部署，行为不变。以后要打开，改 `NotificationConfig` 的默认值再部署。
