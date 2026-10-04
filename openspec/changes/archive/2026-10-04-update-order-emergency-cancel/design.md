# Design

## Context

现状（变更 5 到 7 之后）：

- `Order.cancel_due(now)`：已提交或部分成交、到了截止时刻、还没请求过撤单时为真。`_advance` 每次跟进时先查询，再按它请求撤单，成功后记下 `cancel_requested`
- 没受理过又查不到的订单（`unsent`）：截止时刻之前提交，卖单要先等止损单让出股数；之后结束为已过期
- 券商回报撤销或失效：截止时刻之前记为已撤销，之后记为已过期
- 状态未知的订单满等待时长（默认 30 秒）之前不调用券商
- 止损目标 = 券商持仓 − 未结束卖单的未成交股数（`stop_target`），未结束卖单的合计超过持仓时卖单不提交
- 待提交和状态未知的订单让这只股票暂停新订单（`blocks_new_orders`）
- 执行模块不调用风控；预留由 M2 的下单工作流按订单的最新状态更新（订单状态机的 design D9）

动机见 proposal.md 的 Why，要求见 specs/order-lifecycle/spec.md。

### 模块边界

只改执行模块，不新增也不修改跨模块传递的 Pydantic 模型。`Order` 是执行模块的 frozen dataclass，增加一个字段。

```
modules/execution
  domain/order.py   Order 增加 cancel_wanted、want_cancel、cancel_unsent；cancel_due 加上撤单要求
  service.py        ExecutionService 增加 cancel_orders、cancel_buy_orders；跟进时有撤单要求的不提交
```

## Goals / Non-Goals

**Goals:**

- 撤单要求只是订单上的一个标记，之后全靠已有的跟进：查询、请求撤单、状态未知的等待和重试、状态映射都不另写
- 截止时刻之前撤掉的记为已撤销，和到期失效分开
- 撤单要求先保存再调用券商，中途退出也不会丢

**Non-Goals:**

按「编程风格」省略的情况：

- 取消撤单要求：不支持，撤了就不再提交
- 撤单请求被接受以后券商又报撤不了（订单已经在成交）：按之后的回报处理
- 有撤单要求的订单被券商拒绝：按已有规则记为已拒绝
- 撤单的同时又为这只股票提交新订单：调用方串行，紧急退出工作流先撤再下

## Decisions

### D1 撤单要求记在订单上

```python
@dataclass(frozen=True, slots=True)
class Order:
    ...  # 已有的字段不变
    cancel_requested: bool = False
    cancel_wanted: bool = False  # 有撤单要求：跟进时不再提交，挂着的就请求撤单

    def want_cancel(self) -> Self:
        """记下撤单要求；终态的订单原样返回。"""

    def cancel_due(self, now: datetime) -> bool:
        """要请求撤单了：已提交或部分成交，到了截止时刻或者有撤单要求，还没请求过撤单。"""

    def cancel_unsent(self) -> Self:
        """有撤单要求，截止时刻之前确认券商没收到这笔订单：结束为已撤销。"""
```

- 到期撤单和提前撤单共用 `cancel_requested`，所以每笔订单只请求一次撤单
- 撤单请求超时或断线时，订单按已有规则记为状态未知，`cancel_requested` 仍为假、`cancel_wanted` 仍为真，满等待时长后查清了再请求

考虑过：把截止时刻提前到现在

- 现有的到期撤单、截止后不再提交都能直接用上，改动最小
- 没有采用：撤掉的订单会记成已过期，和到期失效混在一起，交易记录和复盘分不清是被撤的还是到期的。订单状态机的 design 也写了紧急撤单记为已撤销

考虑过：直接调用券商撤单，不在订单上记下

- 没有采用：待提交、状态未知的订单当时撤不了；重启以后撤单的意图就丢了，等止损单让出的卖单还可能被提交出去

### D2 跟进时怎样处理撤单要求

```
_track(plan_id)
  卖单还没提交出去、并且没有撤单要求：先对齐止损单，看是否已经让出股数（不变）
  _advance
    查到                        -> 按回报更新（不变）
    受理过、查不到              -> 状态未知；对账时按挂单结束（不变）
    没受理过、查不到：
      当前时刻 >= 截止时刻      -> 已过期（不变）
      有撤单要求                -> cancel_unsent，已撤销（新增）
      止损单已让出              -> 提交（不变）
    order.cancel_due(now)       -> 请求撤单（条件加上撤单要求）
  之后对齐这只股票的止损单（不变）
```

- 有撤单要求的卖单不用先对齐止损单：反正不提交
- 有撤单要求的卖单在结束之前，仍然算作未结束卖单，止损目标照样扣掉它。撤单生效之前券商还可能成交，止损单不能先改大
- 同一个方案再次提交（`submit`）只跟进已有的订单，走同一条路径，不会再提交

### D3 两个入口

```python
class ExecutionService:
    def cancel_orders(self, symbol: str) -> tuple[Order, ...]:
        """撤掉这只股票全部没结束的订单（买单和卖单），返回跟进一次之后的这些订单。"""
        return self._cancel(o for o in self._store.open_orders() if o.symbol == symbol)

    def cancel_buy_orders(self) -> tuple[Order, ...]:
        """撤掉全部没结束的买单，返回跟进一次之后的这些订单。"""
        return self._cancel(o for o in self._store.open_orders() if o.side is OrderSide.BUY)

    def _cancel(self, orders: Iterable[Order]) -> tuple[Order, ...]:
        """先给每笔订单记下撤单要求并保存，再各跟进一次。"""
        targets = tuple(orders)
        for order in targets:
            if not order.cancel_wanted:
                self._store.save(order.want_cancel())
        return tuple(self.track(o.plan_id) for o in targets)
```

- 先全部保存、再逐笔跟进：中途退出时，撤单要求都已经记下，重启以后的跟进会接着撤
- 不检查对账和交易时段：撤单只会减少风险。重启以后还没对账时也要能撤；状态未知的订单收盘后才查清的，照样按截止时刻记为已过期
- 返回跟进之后的订单：工作流用它们的方案 ID 继续跟进到终态、释放预留，不需要另开查询接口

考虑过：只提供按方案 ID 撤单

- 没有采用：工作流不知道这只股票有哪些订单，要另开一个查询接口；哪些订单该撤由执行模块定，也在这里测

考虑过：一个入口，带股票和方向两个可选参数

- 没有采用：只有这两种用法，两个入口更清楚

### D4 和止损单、预留、紧急退出工作流的关系

- **止损单**：撤掉的卖单结束以后，止损目标恢复，按已有的对齐规则把止损单改回去；紧急清仓的卖单提交前再把它撤掉。多一次改单和一次撤单，但这段时间持仓一直有止损单
- **撤掉的买单**：部分成交的，成交的部分照常挂止损单，紧急清仓会一并卖掉
- **预留**：执行模块仍不调用风控。M2 的下单工作流在订单结束时按累计成交调用"上报成交"和"释放"
- **紧急退出工作流**要等返回的订单都结束、预留都释放，再对清仓方案做风控检查，否则可卖数量还被占着。就算没等，卖单合计超过持仓时执行模块也不会提交（止损目标的未覆盖股数），不会超卖
- **暂停**：有撤单要求的待提交、状态未知订单，仍然让这只股票暂停新订单，直到离开这两种状态。这时紧急清仓的提交报暂停，工作流按已有的处理，等一会儿从再校验重新开始

### D5 测试

```
tests/modules/execution/domain/test_order.py     want_cancel、cancel_due、cancel_unsent
tests/modules/execution/test_cancel_service.py   spec「提前撤单」的场景和「重启后继续提前撤单」
tests/modules/execution/test_service.py          订单状态机增加"提前撤单"这一步
tests/modules/execution/test_protection_service.py
                                                 止损保护状态机增加"撤掉 BBB 的订单"这一步
```

- 测试替身不用改：假券商已经有撤单，能安排撤单失败
- 订单状态机（只有 P1 一笔买单）：
  - 增加一步：拨动时间后撤掉 BBB 的订单
  - 增加不变式：有撤单要求以后，券商不再收到 P1 的提交
  - 已有的不变式照样成立：撤单成功最多一次，券商最多一笔 P1，查询后和券商一致；结束时一定进入终态
- 止损保护状态机：
  - 增加一步：撤掉 BBB 的订单，买单和卖单都会被撤
  - 已有的不变式检查撤卖单的过程中止损单和卖单合计不超过持仓；结束时止损单对齐到目标
- 用 `event` 统计样例里出现过提前撤单结束为已撤销、等让出的卖单被撤

## Risks / Trade-offs

- [状态未知的订单要满等待时长才能撤] → 默认 30 秒，紧急退出是分钟级；断线期间本来也撤不了
- [撤单请求之前或之后订单又成交] → 成交照常计入累计成交；清仓按再校验时的实时可卖数量
- [moomoo 改单和撤单限频：每 30 秒 20 次] → 一次熔断最多 8 只持仓，每只撤一两笔订单、改撤一次止损单，在限频以内；M7 的适配器按限频排队
- [撤掉卖单后止损单先改大、紧急清仓时再撤掉] → 多两次券商调用，换来这段时间持仓一直有止损保护

## Migration Plan

`Order` 增加一个默认为假的字段。订单还没有持久化的数据，不需要迁移；M2 的订单 `repo.py` 带上这个字段。需要回滚时直接 revert。
