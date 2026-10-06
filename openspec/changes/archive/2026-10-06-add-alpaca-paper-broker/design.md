# Design

## Context

现状（M1 和 `add-trade-records` 之后）：

- 执行模块的两个券商端口在 `modules/execution/ports.py`：`Broker` 7 个操作，`StopBroker` 5 个操作。约定都写在端口的说明里，测试替身 `tests/fakes/broker.py`、`tests/fakes/stops.py` 按约定实现
- 券商回报只有 5 种状态（`BrokerOrderStatus`）；已提交还是部分成交、已撤销还是已过期，由执行模块按累计成交和截止时刻判断，适配器只做翻译（`add-order-state-machine` 的 design D4）
- 超时和断线抛 `BrokerUnavailableError`，拒绝受理抛 `OrderRejectedError`，之后怎样重试由执行模块按状态决定
- `integrations` 里只有日历和系统时钟，项目还没有 HTTP 依赖

Alpaca Trading API（2026-10-05 查阅文档，账户只给系统用）：

- REST + JSON，请求头带 `APCA-API-KEY-ID` 和 `APCA-API-SECRET-KEY`；模拟盘地址 `https://paper-api.alpaca.markets`；限频每分钟 200 次
- 订单的股数和价格都是字符串；状态有 17 种
- 改单（`PATCH /v2/orders/{id}`）生成新订单：旧单 `status=replaced`、`replaced_by` 指向新单，新单 `replaces` 指回旧单；订单处于 accepted、pending_new、pending_cancel、pending_replace 时不能改单
- GTC 订单 90 天后自动撤销
- 防自成交保护：同一只股票挂着止损卖单时新买单被拒，挂着限价买单时新止损卖单被拒，返回 403。组合单（bracket、OCO）和跟踪止损单例外
- 2026-06-04 起 FINRA 取消了 PDT 规则，Alpaca 不再按日内交易次数拒单
- 账户配置 `PATCH /v2/account/configurations`：`no_shorting`、`max_margin_multiplier`（"1"、"2"、"4"）

动机见 proposal.md 的 Why，要求见 specs/alpaca-broker/spec.md。

### 模块边界

只在 `integrations` 新增一个包，不改 `modules`、`contracts`、`workflows`、`bootstrap`：

```
integrations/alpaca/
  __init__.py   导出 AlpacaConfig、AlpacaClient、AlpacaBroker、AlpacaStopBroker、configure_account
  client.py     AlpacaConfig；AlpacaClient：发请求、分类失败、解析订单、沿替换找订单；状态映射
  broker.py     AlpacaBroker：实现 Broker
  stops.py      AlpacaStopBroker：实现 StopBroker
```

- 用到执行模块包根导出的：`Order`、`OrderSide`、`BrokerOrder`、`BrokerOrderStatus`、`StopOrder`、`StopReport`、`WorkingOrder`、`BrokerAccount`、`BrokerPosition`、`BrokerUnavailableError`、`OrderRejectedError`；还有 `contracts.trade_plan.OrderType`
- 不新增、也不修改跨模块传递的 Pydantic 模型。解析 Alpaca JSON 的 Pydantic 模型（`AlpacaOrder`、`AlpacaPosition`、`AlpacaAccount`）只在适配器内部用，不导出
- 适配器不读时间，不需要 Clock

## Goals / Non-Goals

**Goals:**

- 执行模块原样跑在 Alpaca 上：适配器的行为和测试替身一致，Alpaca 的差别（改单换订单号、状态多、不收费）都在适配器里消化
- Alpaca 文档没写清的地方，在真实模拟盘上验证一次

**Non-Goals:**

按「编程风格」省略的情况：

- 改单还在进行中时又要改单：Alpaca 不允许，执行模块立即重试一次，再被拒就标记无保护仓位，下一次对齐再改。只在几秒内连续改两次时出现
- 收盘后新挂的止损单在开盘前是 accepted，不能改单；收盘后持仓不会变，不需要改
- 读取账户分两次请求（账户和持仓），中间正好有成交时这一次可能对不上，下一次读取就一致
- 全部挂单只读一页（最多 500 笔），只给系统用的账户不会有这么多挂单
- 沿替换找订单每一跳查一次，不缓存，一条链只有几笔
- calculated 状态（当天已完成、结算计算中）按未结束处理，之后会变成 filled 或其他终态
- 不核对 Alpaca 回报的股票、方向和执行模块的订单是否一致，账户只给系统用
- 清空状态库却没重置模拟盘账户时，止损单的客户端订单 ID 会和旧订单重复、挂单被拒：写进部署说明，代码不处理
- 名义金额（notional）订单：系统不下这种单
- 价格不在适配器里按最小变动单位取整：取整后券商的止损价和止损目标对不上，会反复改单；由方案格式校验负责（proposal「对后续变更的影响」）

## Decisions

### D1 用 httpx 直接调 REST，不用 alpaca-py

只用到 9 个接口：

| 接口 | 用在 |
| --- | --- |
| `GET /v2/orders:by_client_order_id` | 查询订单；没受理过的止损单 |
| `GET /v2/orders/{id}` | 受理过的止损单；沿替换找订单 |
| `POST /v2/orders` | 提交订单、挂止损单 |
| `PATCH /v2/orders/{id}` | 改止损单 |
| `DELETE /v2/orders/{id}` | 撤单 |
| `GET /v2/orders?status=open&limit=500` | 全部挂单、全部挂着的止损单 |
| `GET /v2/positions`、`GET /v2/positions/{symbol}` | 持仓、账户 |
| `GET /v2/account` | 账户 |
| `PATCH /v2/account/configurations` | 账户设置 |

用 httpx 的同步客户端：有类型标注，超时可以配置，自带 `MockTransport`，测试不用另装 mock 库。执行模块是同步的，M2 的 DBOS 工作流也是同步的。

考虑过：

- alpaca-py：要带进 pandas 等一串依赖；它的异常还是要再分一次类；测试也还是要在 HTTP 层模拟。省下的只是拼 URL
- requests：没有自带的 mock transport，测试要再加 responses 之类的库

### D2 AlpacaClient 负责请求和失败分类

`AlpacaClient` 包住 `httpx.Client`，所有请求经过一个私有方法，统一处理 spec「调用失败的分类」的第一行：

```python
def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
    try:
        response = self._http.request(method, path, **kwargs)
    except httpx.TransportError as error:  # 超时也是 TransportError
        raise BrokerUnavailableError(f"{method} {path}：{error}") from error
    if response.status_code == 429 or response.status_code >= 500:
        raise BrokerUnavailableError(f"{method} {path}：{response.status_code}")
    return response
```

其余几行由各个方法按自己的约定处理：

| 方法 | 404 | 其他 4xx |
| --- | --- | --- |
| 查询订单、查询一只股票的持仓 | 查不到（None） | 原样报错（`raise_for_status`） |
| 提交订单、改单 | 拒绝受理 | 401 原样报错，其他拒绝受理（`OrderRejectedError`，带上 Alpaca 的 message） |
| 撤单 | 正常返回 | 422 正常返回，其他原样报错 |
| 读取全部挂单、全部持仓、账户，账户设置 | 原样报错 | 原样报错 |

401 不算拒绝：key 错了，所有调用都会失败，原样报错让启动时就暴露，而不是把每个方案都记为被拒。

配置用 Pydantic 模型，非法值在构造时就报错：

```python
PAPER_URL = "https://paper-api.alpaca.markets"


class AlpacaConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    secret_key: SecretStr
    base_url: str = PAPER_URL
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))
```

`AlpacaClient(config, transport=None)`：`transport` 只给测试传 `httpx.MockTransport`。key 由「启动与装配」从环境变量读出来再构造配置。

考虑过：适配器自己重试几次再报不可用。执行模块已经按状态未知等一段时间再查询，适配器再重试会让一次调用卡很久，还可能重复提交。

### D3 Alpaca 订单的解析和状态映射

```python
class AlpacaOrder(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    client_order_id: str
    symbol: str
    status: str
    type: str
    side: str
    qty: Decimal | None
    filled_qty: Decimal
    filled_avg_price: Decimal | None
    stop_price: Decimal | None
    replaced_by: str | None
    replaces: str | None
```

- 数值字段声明为 `Decimal`，Pydantic 直接从字符串精确转换；股数再用 `int()` 转成整数
- `status` 用 `str` 而不是枚举：Alpaca 以后加了新状态，解析也不会失败，按 spec 映射为结果未知
- `qty` 按 Alpaca 的定义可以为空（名义金额订单），本系统的订单都有股数

状态映射是一个字典，按 spec「订单状态的映射」的表，查不到的给 `BrokerOrderStatus.UNKNOWN`。`replaced` 不在字典里，因为沿替换找到的现在这一笔不会是 replaced（D4）。

### D4 沿替换找到现在的那一笔，累计整条链的成交

```python
@dataclass(frozen=True, slots=True)
class Chain:
    """一笔订单和改单产生的全部替换。"""

    current: AlpacaOrder  # 没有被替换的那一笔
    filled: int  # 整条链的已成交股数之和
    average_price: Decimal | None  # 按股数加权；没有成交时为 None


def chain(self, found: AlpacaOrder) -> Chain:
    """从查到的这一笔往前沿 replaces、往后沿 replaced_by，每一跳查一次 GET /v2/orders/{id}。"""
```

- 往前也要找：止损单记下的是最近一次查到的订单号，比它更早的那几笔的成交也要算进累计成交，端口要求包括改单之前的成交
- 往后找到 `replaced_by` 为空的那一笔。旧单正在改单（pending_replace）、还没有 `replaced_by` 时，现在这一笔就是旧单，回报旧的股数，执行模块记着的改单请求继续等
- 普通订单和止损单都走这一步。普通订单从不改单，`replaces` 和 `replaced_by` 都为空，不多查
- 成交均价 = Σ(已成交 × 成交均价) / Σ已成交，用 Decimal 默认精度。同样的输入算出同样的值，状态库按内容判断有没有变化时不会多写版本

考虑过：只看现在这一笔的成交，让执行模块自己把旧单的成交加上。执行模块不知道 Alpaca 会换订单号，moomoo 是原地改单，这个差别应该留在适配器里（端口说明已经这样约定）。

### D5 两个适配器

```python
class AlpacaBroker:
    def __init__(self, client: AlpacaClient, commission: Decimal) -> None: ...


class AlpacaStopBroker:
    def __init__(self, client: AlpacaClient, commission: Decimal) -> None: ...
```

两个适配器共用一个 `AlpacaClient`（一个连接池）。每个操作对应的请求：

| 端口操作 | 请求 |
| --- | --- |
| `Broker.find` | 按客户端订单 ID 查询，查到后走 D4 |
| `Broker.place` | `POST /v2/orders`：symbol、qty、side、type（limit 带 limit_price，或 market）、`time_in_force=day`、client_order_id；回报从返回的订单生成 |
| `Broker.cancel` | `DELETE /v2/orders/{broker_order_id}` |
| `Broker.position` | `GET /v2/positions/{symbol}`，404 为 0 |
| `Broker.working_orders` | 未结束订单里 `type != "stop"` 的 |
| `Broker.all_positions` | `GET /v2/positions`，股数大于 0 的 |
| `Broker.account` | `GET /v2/account` 和 `GET /v2/positions` |
| `StopBroker.find` | 有券商订单号按订单号查，否则按客户端订单 ID 查，查到后走 D4 |
| `StopBroker.place` | `POST /v2/orders`：side=sell、type=stop、stop_price、`time_in_force=gtc`、client_order_id |
| `StopBroker.modify` | `PATCH /v2/orders/{broker_order_id}`：qty、stop_price |
| `StopBroker.cancel` | `DELETE /v2/orders/{broker_order_id}` |
| `StopBroker.working_stops` | 未结束订单里 `type == "stop"` 的，去掉被列表里另一笔替换的旧单 |

- 股数和价格都按字符串发出（`str(Decimal)`），不经过浮点数
- 止损单的未成交股数 = 现在这一笔的 `qty − filled_qty`。改单时传的股数就是止损目标，也就是要保护的未成交股数，所以替换后的新单 `qty` 就是未成交股数
- 改单不传新的客户端订单 ID，由 Alpaca 生成：受理过的止损单按订单号查，用不到客户端订单 ID；对账只比较止损单的张数

### D6 费用估算

```python
def _fees(self, filled: int) -> Decimal:
    return self._commission if filled > 0 else Decimal(0)
```

`filled` 是整条链的累计成交，所以止损单连同替换只收一笔。`commission` 由「启动与装配」传入 `RiskConfig.commission_per_order`，和风控估算用的是同一个值（`add-trade-records` 的 design D5）。

考虑过：按每一笔有成交的 Alpaca 订单各收一笔。改单是本系统内部的操作，实盘 moomoo 原地改单也只收一笔，按逻辑上的一张止损单算更接近实盘。

### D7 账户设置

```python
def configure_account(client: AlpacaClient) -> None:
    """不做空、最大保证金倍数 1：按现金账户使用。可以重复调用。"""
```

`PATCH /v2/account/configurations`，body 是 `{"no_shorting": true, "max_margin_multiplier": "1"}`，只改这两项，其他配置不动。由「启动与装配」在启动时、对账之前调用；失败就报错，启动中止。

考虑过：

- 写进部署步骤、由人在 Alpaca 网页上设一次：容易忘，换一个模拟盘账户还要再设
- 启动时只读不改、不对就拒绝启动：比直接设置多一种失败，人还得去网页上改

### D8 适配器不绕开防自成交保护，由执行模块错开挂单

见 proposal「需要人决定」第 1 条。适配器把 403 和其他拒绝一样当作拒绝受理，不撤、不改别的挂单。避开这种拒绝放在执行模块：下一个变更「买单和止损单错开」加一项执行配置，Alpaca 上打开以后，一只股票有买单挂着时先撤掉止损单、由本地备用止损盯着，买单结束后再挂止损单。

放在执行模块而不是适配器：撤哪张止损单、什么时候再挂，都是止损保护的状态，只有执行模块知道；适配器背着执行模块撤单，下一次对齐会把它当作被券商撤销，马上重挂，又被拒。

考虑过：

- 适配器在提交买单前自己撤掉止损单：理由同上
- 把止损单换成 Alpaca 豁免的组合单（OCO）：豁免的细节没有文档，组合单改不了股数，止损单还要带一条止盈腿

### D9 测试

- `tests/integrations/alpaca/payloads.py`：从 Alpaca 文档的样例里抄下订单、账户、持仓的完整 JSON，测试只覆盖需要的字段
- `tests/integrations/alpaca/server.py`：`FakeAlpaca`，一个内存里的 Alpaca。按订单号保存订单，有持仓、账户、账户配置；`handler` 按方法和路径分发，交给 `httpx.MockTransport`；记录收到的每个请求；可以安排下一次某个请求返回给定的状态码和 message，或者抛 `httpx.ConnectTimeout`、`httpx.ConnectError`
- 每个 spec 场景一个测试，名字或说明里写明场景
- 类型检查确认适配器满足端口：测试里写 `broker: Broker = AlpacaBroker(...)`，`uv run mypy` 会检查
- 冒烟测试 `tests/integrations/alpaca/test_paper_smoke.py`：连真实的模拟盘，加 `alpaca_paper` 标记。pytest 默认配置加 `-m "not alpaca_paper"`，所以平时和 CI 都不运行；`uv run pytest -m alpaca_paper` 才运行，没有 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY` 环境变量时跳过。验证文档没写清的几点：查不到时返回 404、改单后新单的股数和 `replaces`、防自成交返回 403、账户设置生效。需要开盘的步骤（买 1 股、挂止损单、改单、撤单、卖掉）在休市时跳过

考虑过：录下真实模拟盘的回报存成文件。要先有账户和 key，而且 Alpaca 的订单 JSON 字段很稳定；文档样例加上冒烟测试已经够用。

## Risks / Trade-offs

- [Alpaca 文档没写清的行为：查不到订单时的状态码、改单后新单的股数] → 冒烟测试在真实模拟盘上验证；和 spec 不符时先改 spec 和 design，再改代码
- [本变更实现以后、「买单和止损单错开」实现之前，加仓买单被拒，部分成交时止损单挂不上] → 两个变更紧挨着做，中间不接真实下单的流程
- [错开以后，模拟盘上加仓和部分成交期间没有券商止损单，和实盘不同] → 由本地备用止损按同一个止损价盯着；这段时间只到买单结束
- [限频每分钟 200 次] → 跟进和对齐的频率在「下单流程」里定；一次对齐通常 2 到 3 个请求
- [模拟盘和实盘行为不同：不收费、保证金账户、随机部分成交、防自成交] → 尽量接近实盘：费用按实盘的每笔佣金估算，账户设成不做空、不加杠杆，已结算资金按 T+1 自己算，初始资金设成实盘打算用的金额（「部署」）；其余差别在 M7 验证

## Migration Plan

- 新增依赖 httpx；没有数据库迁移
- 回滚：revert 代码即可，其他代码还没有用到这个包
