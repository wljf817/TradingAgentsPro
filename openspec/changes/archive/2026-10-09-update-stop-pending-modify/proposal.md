# Proposal

## Why

Alpaca 改单（`PATCH /v2/orders/{id}`）时生成一笔新订单替换旧单，替换完成以前旧单处于 `pending_replace`。Alpaca 文档写明：`pending_replace` 的订单不能再改单（"Order cannot be replaced when the status is accepted, pending_new, pending_cancel or pending_replace"），也拒绝撤单（"The order will reject cancel request while in this state"）。替换没完成时，查询旧单查到的还是旧股数（alpaca-broker spec「改单产生的替换」）。

执行模块现在按 stop-protection spec「对齐止损单」：改单请求的回报还没反映时，下次对齐会再请求一次（`next_action` 只要券商回报的股数和目标不同就给出改单）。所以同一只股票几毫秒内对齐两次时，第二次又发一次改单，被拒，立即重试再被拒，这只股票就被标为无保护仓位并告警。会这样连续对齐的地方：

- `ExecutionService.reconcile()`：第 3 步跟进订单时对齐一次，第 4 步又对齐一次（比如收盘失效的卖单在跟进时把止损单改回剩下的持仓）
- `_track`：还没提交出去的卖单先对齐一次，订单在这次跟进里有变化（比如到了截止时刻）又对齐一次
- 下单流程（`add-order-flow`）提交卖单时对齐，几百毫秒内巡检又对齐同一只股票
- 收盘对账（`add-close-reconciliation`）紧跟在巡检一轮的对齐之后

盘中巡检只在自己的一轮里绕开了（第 3 步不再对齐第 1 步跟进过的股票，`add-session-patrol` 的 design D4）。M2 的完成标志要求 10 个交易日对账无差异，当天出现过的无保护仓位算差异，误标会让连续天数清零。

本变更的目的只有一个：**止损单的改单请求还没生效时，执行模块对齐时不再改单，等之后的对齐再看。**

引用的 Proposal（完整版）章节，「订单生命周期」表的四行：

- 止损单挂单失败：立即重试，仍失败则标记无保护仓位、告警
- 主动减仓或清仓：先改止损单（清仓则撤销），券商确认后再下卖单；卖单结束后恢复为剩下的持仓
- 加仓：成交后把止损单改为合计数量；改单失败按挂单失败处理
- 止损单有效期：GTC，被券商撤销或到期时重新挂上

## What Changes

- 执行模块记下最近一次改单请求的股数，并且记下请求的时刻
- 止损单挂着、最近一次改单请求还没生效（券商回报的股数还不等于请求的股数）、从请求的时刻起还没满等待时长时，这个请求"在等待生效"。这时对齐只查询和更新回报，**不改单，也不撤单**，不管止损目标是什么；不算改单被拒，不标记无保护仓位
- 满等待时长还没生效的请求不再等（请求超时而券商没收到、券商没有执行这次改单等）：之后的对齐照常按目标再改单一次，重新记下股数和时刻。券商真的一直卡在改单中时，再改单被拒，按已有的「挂单或改单被拒」标记无保护仓位
- 等待时长用已有的「状态未知的等待时长」（默认 30 秒），不新增配置项
- 卖单先让出股数、买单等止损单撤掉的判断不变：改单请求生效以前不算让出，在等待生效时也不撤单，卖单或买单等之后的跟进再确认
- 改单超时或断线时，照旧记下请求的股数，同时记下请求的时刻

## Non-goals

- 盘中巡检的绕开（第 3 步不再对齐第 1 步跟进过的股票）保留，不改 session-patrol spec
- 不改 Alpaca 适配器和 alpaca-broker spec；不在券商回报里加"正在改单"的状态（见 design D1）。测试用的 FakeAlpaca 只改一处：正在改单（pending_replace）的订单撤单时返回 422，和 Alpaca 文档一致
- 不改 order-lifecycle spec「执行配置」：复用状态未知的等待时长，不新增配置项（见 design D3）
- 不改下单流程、收盘对账的文档：它们 design 里记下的这个风险，在本变更落地以后不再出现
- 撤单请求还没生效时又要改单、新挂的止损单还没路由出去时就要改单：Alpaca 同样不接受，但只在目标几秒内连续变化时出现，见 design 的省略清单
- moomoo 实盘（M7）原地改单，这条规则对它无害，不需要开关
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

无

### Modified Capabilities

- `stop-protection`：
  - 「对齐止损单」：改单请求在等待生效时不改单、不撤单；记下改单请求的时刻，满等待时长还没生效时再改单
  - 「卖单先让出股数」：改单请求在等待生效时卖单等待；改大的请求还没生效时、清仓时的场景按新规则改写
  - 「止损单的券商调用失败」：改单超时也记下请求的时刻；增加"改单超时，券商没有收到"的场景
  - 「保存止损保护」：保存改单请求的时刻；增加重启后仍在等待生效的场景

## Impact

- **代码**：全部在 `modules/execution/`
  - `domain/protection.py`：`StopOrder` 增加改单请求的时刻；判断改单请求是否在等待生效；`next_action` 在等待生效时不改单、不撤单
  - `service.py`：改单时记下时刻；对齐时把当前时刻和等待时长交给 `next_action`
  - `config.py`：状态未知的等待时长的注释写上新的用途
- **测试**：
  - `tests/modules/execution/domain/test_protection.py`：等待生效的边界、`next_action`
  - `tests/modules/execution/test_protection_service.py`：spec 的场景；止损保护状态机增加一条性质
  - `tests/modules/execution/test_reconciliation_service.py`：对账时跟进订单改了单，第 4 步不再改单
  - `tests/modules/execution/test_repo.py`：改单请求的时刻存取，旧记录读出来没有时刻
  - `tests/integrations/alpaca/server.py`：FakeAlpaca 对正在改单的订单撤单返回 422；`tests/integrations/alpaca/test_stops.py` 加对应的测试
- **存储**：止损保护的记录多一个字段，旧记录读出来没有请求时刻（当作已经满等待时长，和现在的行为一样），不用迁移
- **依赖**：无
- **文档**：无
- **实施时机**：「收盘对账」改 `modules/execution/service.py`、`ports.py`、`repo.py`，本变更等它落到 main 以后再 apply（已落地）。「下单流程」只改 `workflows/` 和 `bootstrap/`，不用等
- **对后续变更的影响**：
  - 「下单流程」「收盘对账」：提交卖单后巡检马上对齐、收盘对账紧跟巡检对齐，不再误标无保护
  - 「紧急退出流程」：清仓时碰上改单请求在等待生效，撤止损单和提交卖单要等改单生效（Alpaca 上通常不到一秒，最多等待时长）
  - M7：moomoo 原地改单，改单请求通常在下一次查询时已经生效，这条规则只在还没反映时让对齐少发一次重复的改单，无害

## 需要人决定

无
