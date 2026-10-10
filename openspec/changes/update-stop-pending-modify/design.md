# Design

## Context

现状（`add-session-patrol` 之后）：

- 对齐止损单（`ExecutionService._align`）：先查询当前止损单、按回报更新，再查询持仓、算止损目标，按 `next_action` 挂单、改单、撤单或放弃（`add-stop-protection` 的 design D4）
- `StopOrder.requested` 是最近一次改单请求的股数，券商回报的股数等于它时清掉。`is_aligned` 要求券商回报的股数、止损价等于目标，并且请求的股数是空或等于目标；不对齐就给出改单。所以请求还没生效时，每次对齐都再发一次同样的改单。`add-stop-protection` 的省略清单原样写着："改单请求还没反映到回报：下次对齐再发一次同样的改单，不记录'已经请求过'"
- `stop_yields`（卖单先让出股数）要求 `requested` 为空；`stop_withdrawn`（错开时买单等止损单撤掉）只看止损单是否结束
- 改单被拒：立即重试 `stop_immediate_retries` 次，都被拒就标记无保护仓位，不动 `requested`（D6）。改单超时或断线：记下 `requested`，再记为状态未知（D9），等 `unknown_lookup_delay` 再查
- `Protection` 连同 `StopOrder` 整条经 pydantic `TypeAdapter` 存成 JSONB（state-store spec）

Alpaca（2026-10-09 查阅文档）：

- 改单（`PATCH /v2/orders/{id}`）返回替换它的新订单；"A success return code from a replaced order does NOT guarantee the existing open order has been replaced"。旧单替换完成以前是 `pending_replace`
- "Order cannot be replaced when the status is accepted, pending_new, pending_cancel or pending_replace"
- `pending_replace`："The order will reject cancel request while in this state"。撤单接口只写了 204 和 422（"The order status is not cancelable"）
- 适配器：改单的 4xx（401 除外）报拒绝受理；撤单的 404、422 正常返回；查询时旧单还没指向新单，现在这一笔就是旧单，回报旧股数（alpaca-broker spec「改单产生的替换」）
- FakeAlpaca 的改单立即完成，`pending_replace` 只在测试手工设置时出现，所以已有的测试碰不到这个问题

动机见 proposal.md 的 Why，要求见 specs/stop-protection/spec.md。

### 模块边界

只改执行模块，不新增也不修改跨模块传递的 Pydantic 模型：

```
modules/execution
  domain/protection.py   StopOrder 增加 requested_at；request 记下时刻；新增 awaits_modify；
                         next_action 增加 now、wait 两个参数，等待生效时给出不动
  service.py             _act 把当前时刻和 unknown_lookup_delay 交给 next_action；_modify_stop 记下时刻
  config.py              unknown_lookup_delay 的注释写上新的用途
```

`StopOrder` 是执行模块 domain 的 dataclass，从包根导出给 `integrations` 当端口参数用；新字段有默认值，适配器不用改。端口 `Broker`、`StopBroker` 不变。

## Goals / Non-Goals

**Goals:**

- 同一只股票几毫秒内对齐两次（对账的第 3、4 步，`_track` 里的两次对齐，下单流程和巡检，收盘对账和巡检），第二次不改单、不撤单，不误标无保护仓位
- 改单请求一直不生效（超时而券商没收到、券商没执行）时，止损单不会永远对不齐，卖单也不会永远等不到让出
- 判断全部在 domain 里，service 只多传两个参数；已有测试只改 spec 改写了的场景

**Non-Goals:**

- 巡检第 3 步跳过第 1 步跟进过的股票：本变更以后已经不需要，保留不删（session-patrol spec 不改）
- 不改 Alpaca 适配器、FakeAlpaca

按「编程风格」省略的情况：

- 改单请求的股数等于券商当时回报的股数（只改止损价，或者满等待时长以后改回原来的股数）：查询时立即算作生效，几毫秒内目标又变时还会再改单；只在持仓变化正好抵消、或者请求一直没生效以后才出现，Alpaca 上错开打开后止损价只在挂新单时变
- 券商一直停在改单中：满等待时长后再改单被拒，按「挂单或改单被拒」标记无保护仓位并告警，不另外处理
- 撤单请求还没生效时又要改单（Alpaca 正在撤单的订单不能改单）：撤单以后不到一秒目标又变大才出现
- 新挂的止损单还没路由出去时就要改单（Alpaca 的 accepted、pending_new 不能改单）：挂单以后几秒内目标又变才出现
- 本变更之前保存的记录有改单请求、没有请求时刻：当作已经满等待时长，和现在的行为一样

## Decisions

### D1 靠执行模块记下的改单请求判断，不靠券商回报的状态

改单请求"在等待生效"：

```python
def awaits_modify(self, now: datetime, wait: timedelta) -> bool:
    """改单请求在等待生效：挂着，最近一次改单请求还没生效，从请求的时刻起还没满 wait。"""
    return (
        self.status is StopStatus.WORKING
        and self.requested is not None
        and self.requested_at is not None
        and now < self.requested_at + wait
    )
```

`StopOrder` 上的方法，`requested` 照旧在回报的股数等于它时清掉，`requested_at` 跟着一起清。

考虑过：

- 让适配器在止损单回报里带上"正在改单"（Alpaca 的 `pending_replace`），执行模块看到就不改：最准确，异步被拒、超时没收到都自然处理。但要改 `StopReport`、两个适配器（moomoo 也要给出这一项）、`FakeStopBroker` 和 alpaca-broker spec，适配器还要判断"替换以后的新单还没路由出去"这类状态。执行模块已经记着改单请求，加一个时刻就够了
- 在每个调用方避开连续对齐（巡检现在的做法）：对账、`_track`、下单流程、收盘对账都要各自处理，以后的调用方还会再碰上

### D2 等待有上限

满等待时长还没生效的请求不再等，之后的对齐照常按「对齐止损单」的表处理：请求的股数和目标不同、或者券商回报和目标不同时再改单一次，重新记下股数和时刻。

不设上限的话，请求永远不生效的止损单就再也不改了。这不只是券商卡住：

- 改单超时而券商没收到：照旧记下了请求（`_modify_stop` 的做法：券商可能收到了），回报一直是旧股数
- 券商受理以后没执行：Alpaca 文档写明改单成功返回不保证替换完成，比如旧单先成交时替换被拒
- 后果：止损单停在旧股数；`stop_yields` 要求请求已经生效，这只股票之后的卖单（包括紧急退出的清仓）一直等不到让出，到截止时刻过期

满等待时长以后再改单，如果券商其实还在改单中，再改单被拒，按已有规则标记无保护仓位并告警。这时券商已经卡了一个等待时长，告警是对的。

考虑过：改单超时时不记下请求。只解决了超时没收到这一种，异步被拒仍然卡住；还会丢掉 `add-stop-protection` 记下请求的理由（超时的改大请求之后生效，卖单不能提前提交）。

### D3 等待时长复用状态未知的等待时长

`ExecutionConfig.unknown_lookup_delay`，默认 30 秒，不新增配置项。

- 两个等待的意思相同：券商处理完一次请求、结果能查到要多久。止损单状态未知的等待已经复用它（`add-stop-protection` 的 design D9）
- 改单超时时，止损单状态未知和改单请求从同一个时刻开始等。满等待时长后第一次查询，请求也正好不再等：券商没收到的改单在这次对齐里就再发一次（spec 场景「改单超时，券商没有收到」）。两个时长不同时，要么白等一段，要么状态查清以前请求已经不再等，行为更难说清
- Alpaca 替换通常不到一秒完成，巡检 15 秒一轮，30 秒足够；没有单独调它的需要

考虑过：新增「改单生效的等待时长」。要同时改 order-lifecycle spec「执行配置」，路线表里本变更的类型也要多写一项；为一个现在不需要单独调的值，不值得。

### D4 等待生效时也不撤单

`next_action` 在 `awaits_modify` 为真时直接给出不动，排在目标为 0 的撤单之前：

```python
def next_action(
    protection: Protection, target: StopTarget, now: datetime, wait: timedelta
) -> StopAction:
    """对齐时怎样处理当前这张止损单。状态未知的止损单不处理，由调用方先查清；
    改单请求在等待生效时不改单、不撤单。
    """
    stop = protection.stop
    if stop is not None and stop.awaits_modify(now, wait):
        return StopAction.NONE
    ...  # 其余不变
```

- Alpaca 在 `pending_replace` 时拒绝撤单。现在发出去也不出错：多半返回 422，适配器把 422 当作"已经结束"正常返回，下一次对齐看到止损单还挂着会再撤。但文档只说拒绝、没写状态码；返回别的 4xx 会原样抛出，对账和巡检就中断这一项。不发就不依赖这一点，规则也只有一条："在等待生效时不改单、不撤单"
- 代价：清仓（包括紧急退出）、错开时的买单要等改单生效以后才撤止损单。Alpaca 上这段时间撤单本来也会被拒；moomoo 原地改单，最多晚一次跟进
- 挂单、放弃不受影响：等待生效的止损单是挂着的，不会走到这两种处理

### D5 其余规则不变

- `is_aligned`、`stop_yields`、`stop_withdrawn` 不变。满等待时长还没生效的请求仍算没生效，卖单仍不提交，等再改单生效
- `_align` 里解除无保护仓位的条件不变：等待生效时目标为 0 照样解除，目标大于 0 时不对齐也不标记
- 被拒的改单仍不动 `requested` 和 `requested_at`：被拒说明券商那边还是原来的单，之前的请求仍然可能生效，也已经满了等待时长，下一次对齐照常再改

### D6 存储：多一个字段，不用迁移

`StopOrder.requested_at: datetime | None = None`，放在 `requested` 后面。JSONB 里多一个键；旧记录没有这个键，读出来是 `None`，`awaits_modify` 为假，和现在的行为一样。`repo.py` 和迁移不改。

### D7 测试

- `tests/modules/execution/domain/test_protection.py`：`awaits_modify` 的边界（没有请求；有请求、没有时刻；差 1 秒满；正好满；不是挂着）；`request` 记下时刻；`apply` 在请求生效时两个一起清、止损单结束时两个一起清；`next_action` 在等待生效时目标不同、目标为 0 都给出不动，满等待时长以后给出改单、撤单
- `tests/modules/execution/test_protection_service.py`：spec 新增和改写的场景。已有的测试里，「改单的结果以查询为准」「改大的请求还没生效时不提交卖单」按新场景改写，「减仓时先改小止损单」加上时刻和"没有再改单"的断言，其余不改
- `tests/modules/execution/test_reconciliation_service.py`：spec 场景「对账时跟进订单改了单，对齐这只股票时不再改单」
- `tests/modules/execution/test_repo.py`：有改单请求和时刻的止损保护存取不变；数据库里没有 `requested_at` 的旧记录读出来是 `None`（连数据库的部分要开 Docker）
- 止损保护的两个状态机加一条规则"不拨时间、不让请求生效，接着对齐两次"：第一次有被券商接受的改单请求时，第二次没有改单和撤单的调用。两次之间目标不变，所以不管第一次的请求算不算生效，第二次都不该调用。已有的性质（止损单和卖单合计不超过持仓、最终对齐、不是无保护仓位）照常检查

## Risks / Trade-offs

- [收盘以后的改单在 Alpaca 上多久生效，文档没写] → 收盘对账里第二次对齐不再改单，正是本变更处理的情况。如果替换要到第二天开盘才完成，开盘后第一次对齐时请求已经满等待时长，再改单会被拒、标记无保护并告警，下一轮巡检替换已经完成就补上。值得在模拟盘上看一次收盘后改单的状态变化
- [卖单、清仓多等一会儿] → 改单生效以前本来就不能提交卖单（「卖单先让出股数」）；本变更只让这段时间不再发重复的改单和撤单，多等的只是撤单那一步，Alpaca 上替换通常不到一秒
- [等待时长内请求其实已经失败（券商没收到、被拒），止损单晚 30 秒才改] → 这段时间券商止损单是改之前的样子，仍挂着；卖单不会因此提前提交，安全性不受影响
- [实施和别的变更冲突] → 「下单流程」「收盘对账」正在改 `service.py`；本变更等它们落到 main 以后再 apply，改动集中在 `domain/protection.py`，`service.py` 只动 `_act` 和 `_modify_stop` 两处
