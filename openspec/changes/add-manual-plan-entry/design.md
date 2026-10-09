# Design

## Context

现状：

- add-discord-notify（已实现，未归档）：`DiscordNotifier` 在自己的 `discord` 线程上跑事件循环，`DiscordGateway` 把 discord.py 翻译成 `Gateway` 和 `ButtonPress` 两个协议。`_Client.on_interaction` 只把带消息的按钮交互交给 `DiscordNotifier`，别的交互不应答，留给本变更
- `TradePlan`（contracts）在构造时校验格式，规则见 risk-rules spec「交易方案的格式」；`sector` 四种动作都必填。持仓的板块来自最近一笔有成交的开仓订单（`HeldPosition.sector`），卖单的板块只记在订单上，不参与计算
- Ruff 全项目禁用 `uuid.uuid4`（「ID 通过 ID 生成端口获取，回放时才能重现」），但 ID 生成端口还没有。shared 的说明里已经写了它放 ID 和 Clock 这类协议
- 风控的紧急阈值和允许价格偏离有上下限（默认 3% 到 20%、1% 到 10%），超出时风控拒绝，不是格式错误

discord.py（2026-10-08 读 2.7.1 的源码）：

- 斜杠命令用 `app_commands`：`CommandTree(client)` 收到命令类交互时自己解析参数、调用回调（在事件循环上新建一个任务），同时照常派发 `on_interaction`，所以已有的按钮处理看得到命令交互，但它只接按钮
- `app_commands.Group` 可以继承，方法用 `@app_commands.command` 定义子命令；参数的类型标注决定 Discord 的选项类型，有默认值的是可选项；`Range[int, 1]` 让 Discord 客户端检查最小值；`describe` 给每个选项加说明（最长 100 字）
- `tree.sync(guild=...)` 用 PUT 覆盖这个机器人在该服务器的全部命令，立即生效；需要 `client.application_id`，`login` 以后就有。失败时抛 `Forbidden`（比如没有 `applications.commands` 授权）、`HTTPException`、`CommandSyncFailure`
- 命令交互的 `response.defer(ephemeral=True)` 是延迟应答，之后只对本人显示「正在思考」；15 分钟内 `edit_original_response(content=...)` 把它换成回复，仍然只有本人看得到。`response.send_message(..., ephemeral=True)` 直接应答
- Discord 的数字选项是双精度浮点数，180.50 这样的价格不能精确表示

要求见 specs/manual-plans/spec.md 和 specs/risk-rules/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
shared/ids.py                     (新) IdGenerator
contracts/trade_plan.py           (改) TradePlan 加最小变动单位的校验

modules/notification/             (改，只加端口)
  ports.py      PlanIntake
  __init__.py   导出 PlanIntake

integrations/random_ids.py        (新) RandomIdGenerator：全项目唯一调用 uuid4 的地方

integrations/discord/
  config.py     (改) DiscordConfig 加 submit_timeout
  plans.py      (新) PlanEntry、PlanFormatError、build_plan、回复的文字（不做 I/O）
  commands.py   (新) CommandCall（适配器内部的协议）、PlanCommand
  gateway.py    (改) DiscordGateway 多一个参数 plans；_Client 带 CommandTree，登录后注册；
                     PlanGroup（四个子命令的定义）、InteractionCall、注册错误的转换
  __init__.py   (改) 导出 PlanCommand
  notifier.py、messages.py 不变
```

- 模块之间传递的 Pydantic 模型：只用已有的 `TradePlan`（适配器 → `PlanIntake` → 以后的下单流程）和 `RiskDecision`（返回给适配器），不新增 contracts。`PlanEntry` 是适配器内部的 dataclass，不出 integrations
- `PlanIntake` 是通知模块定义、bootstrap 实现、适配器调用的接口，和 `DecisionHandler` 一样（D5）
- integrations/discord 依赖通知模块的包根、contracts、shared（`IdGenerator`、`TradingCalendar`），不依赖 workflows
- 纯区域的白名单不变：shared 的 `ids.py` 只用 `typing` 和 `uuid`（白名单里已有，生成随机 UUID 的函数由 Ruff 禁止）；contracts 只多用 `decimal`
- 风控、执行和其他模块不变；`DiscordNotifier` 和按钮的处理不变

## Goals / Non-Goals

**Goals:**

- 人在手机上也能快速录入：只列这个动作能填的字段，字段名短，价格和百分数照习惯写
- 价格从文字到 `TradePlan` 不经过浮点数
- 命令在 3 秒内应答，提交接口再慢也不卡住按钮和告警
- 适配器的逻辑不连 Discord 就能测：discord.py 的部分只做翻译

**Non-Goals:**

按「编程风格」省略的情况：

- 股票代码不核对是否存在：填错的代码到下单前再校验取不到行情时作废
- 买入、加仓填的板块不核对写法：写错了就是另一个板块；板块上限默认 100%
- 有效期早于现在：不拦，下单前再校验按过期作废
- Discord 重复投递同一次交互：不去重，两个方案 ID 各要一次人工确认
- 提交超时以后才返回的结果只写日志，不补发回复
- 一直不返回的提交接口占着工作线程：不另外限制
- 回复超过 2000 字（比如很长的错误信息）：不截短，发不出去时只写日志
- 在服务器的其他频道运行 `/plan`：照常处理，确认消息仍发到配置的频道
- 别人在命令列表里看得到 `/plan`：不隐藏，运行时拒绝
- 给别人的拒绝提示发不出去：只写日志
- 回滚代码以后服务器里还留着 `/plan`：运行时 Discord 提示没有响应，下次注册时覆盖

## Decisions

### D1 命令的形状

`/plan` 一个命令组，四个子命令 `buy`、`add`、`reduce`、`close`，每个子命令只声明这个动作允许的字段（spec「子命令和字段」的表）。

- 子命令而不是一个 `action` 选项：Discord 只显示这个子命令的字段，必填项由 Discord 检查，「减仓带止损价」这种组合根本填不出来，适配器不用再判断
- 字段名用短的英文（`symbol`、`sector`、`quantity`、`limit`、`stop`、`emergency`、`deviation`、`expires`），说明用中文：英文名在手机键盘上不用切输入法，输入前几个字母就能补全；中文说明显示在每个字段下面，写明怎么填
- 减仓和清仓不设订单类型字段：填了 `limit` 就是限价单，没填就是市价单。少一个字段，也不会出现「市价单带限价」
- `quantity` 用 Discord 的整数选项、最小值 1（`Range[int, 1]`），其余都是文本选项

考虑过：

- 一个命令加 `action` 选项：所有字段都要列成可选，必填和不允许都要适配器检查，Discord 也提示不了
- 中文字段名：每个字段都要切输入法，补全也不方便
- 弹出表单（modal）：最多 5 个输入框，买入有 8 个字段

### D2 价格和百分数的写法

价格是文本选项：Discord 的数字选项是浮点数，180.50 这样的价格不能精确表示，后面又要检查最小变动单位。

```python
_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?")
```

- 去掉首尾空白以后整串匹配 `_NUMBER` 才是数字，再 `Decimal(text)`，保留写的位数（`180.50` 还是 180.50）。`$`、逗号、负号、`1e3`、`.5` 都不是：负号和 0 能写成别的意思，只认一种写法，错了就回复怎么写
- 百分数（紧急阈值、允许价格偏离）按人习惯的百分数写：`8` 和 `8%` 都是 0.08，`7.5` 是 0.075。数字部分同上，后面可以带一个 `%`，结果是 `Decimal(数字).scaleb(-2)`，精确，不经过除法的舍入
- 百分数的格式检查只管「大于 0、小于 100」，和 `TradePlan` 的「大于 0、小于 1」一致；是否在风控的上下限内（比如紧急阈值 3% 到 20%）由风控判断，结果是拒绝并写明规则
- `0.08` 按百分数理解成 0.0008：只有一种理解，风控会因为低于下限拒绝，人看到原因再改。不猜「小于 1 的是比例」，`0.5` 这样的值两种理解都说得通

格式检查分两层：

- `build_plan` 先逐个字段解析：数字的写法、价格大于 0、百分数的范围、有效期、股票和板块不为空。这些原因用人填的文字写，比如「限价「18o.5」不是数字，写成 180.50 这样」
- 再构造 `TradePlan`，它的校验（最小变动单位，以及万一绕过 Discord 的必填和股数检查时的那些规则）抛 `ValidationError`，逐条转成原因：我们自己写的规则（`value_error`）原样用中文说明；Pydantic 的约束（比如 `greater_than`）前面加上字段的中文名

两层都把全部原因收齐再抛 `PlanFormatError`：一次改完，不用改一处、发一次、再看下一处。

### D3 有效期写交易日

`expires` 写 `年-月-日`，表示到这个交易日收盘，结果是 `calendar.session(day).close`（UTC）。

- 先用 `[0-9]{4}-[0-9]{2}-[0-9]{2}` 匹配，再 `date.fromisoformat`；日期不存在（`2026-02-30`）和写法不对一样回复
- 不是交易日回复「不是交易日」；超出日历的覆盖范围（`CalendarRangeError`）回复「超出交易日历的范围」
- 夏令时和提前收盘都由交易日历给出，适配器不算时区

考虑过：

- 写带时区的时刻（`2026-10-16T16:00-04:00`）：手机上难写，时区容易写错；方案的有效期本来就是按收盘算的（风控不填时的默认值就是执行交易日的收盘）
- 不提供这个字段：周末录的方案默认到下一个交易日收盘，大多数情况够用；但想让方案多挂几天时没有办法。有了交易日历，多一个字段的代价很小

`PlanCommand` 因此要注入 `TradingCalendar`，由「启动与装配」传入同一份日历。

### D4 方案 ID 和 ID 生成端口

每条命令（本人的、延迟应答成功以后）先取一个新的方案 ID，再查持仓和解析；格式错误的命令也占用一个 ID，日志里能对上。

```python
class IdGenerator(Protocol):
    """新 ID 的来源。和 Clock 一样由 bootstrap 注入：回放时换成能重现的实现。"""

    def new_id(self) -> UUID:
        """一个新的 UUID，和之前给出的都不同。"""
        ...
```

```python
class RandomIdGenerator:
    """随机 UUID（版本 4）。全项目只有这里可以调用 uuid4。"""

    def new_id(self) -> UUID:
        return uuid4()
```

- 端口放 shared：它和 Clock 一样是全系统的基础协议，以后模型给出的方案（M4、M5）、案件和任务的 ID 也从这里取；shared 的说明本来就写了放 ID
- 实现放 `integrations/random_ids.py`，`pyproject.toml` 的 Ruff 只对这个文件放行 `uuid.uuid4`（TID251），和 `system_clock.py` 读系统时间是同一个做法
- 测试用 `tests/fakes/ids.py` 的 `FakeIds`，依次给出 `UUID(int=1)`、`UUID(int=2)`……，场景里的 U1、U2 就是它们
- ID 在适配器里取，因为适配器是创建方案的一方（`TradePlan` 的说明：方案 ID 由创建方案的一方生成）。以后「下单流程」用它作 DBOS 工作流 ID：ID 是工作流的输入，在工作流之外生成，工作流保持确定性；同一个 ID 只启动一次，重复提交不会下两次单

考虑过：

- 在适配器里直接调用 `uuid4` 并放行：每个创建方案的地方都要放行一次，回放也换不掉
- 用 `secrets` 拼 UUID 绕开 Ruff：违背禁用的本意

### D5 提交接口 PlanIntake

```python
class PlanIntake(Protocol):
    """人手工录入的方案交给谁：由 bootstrap 实现，通知通道调用。

    两个方法都在通知通道的工作线程里调用，可以阻塞。
    """

    def held_sector(self, symbol: str) -> str | None:
        """这只股票持仓的板块；没有持仓时为 None。减仓和清仓的方案用它填板块。"""
        ...

    def submit(self, plan: TradePlan) -> RiskDecision:
        """提交方案，风控得出结果就返回，不等人工确认。

        通过或调整的方案由实现方请求人工确认。抛错表示没有拿到风控结果：方案可能已经
        提交，也可能没有。
        """
        ...
```

放在通知模块的 ports：

- 通知模块是系统和人之间的通道（架构图的「人工确认」、技术栈的「通知与人工确认」）。它已经有一个同样方向的接口 `DecisionHandler`：人在 Discord 上操作，适配器调用，bootstrap 把它接到工作流。`PlanIntake` 是同一个人、同一个机器人的另一种操作
- integrations 不能 import workflows，所以接口要由某个模块定义；下单流程实现它时，由 bootstrap 包一层注入

考虑过：

- 新建一个只有端口的模块（比如 `modules/manualplan/`）：架构图里没有这个节点，模块里只有一个协议
- 放 contracts：contracts 放跨模块传递的 Pydantic 模型，不放接口
- 放 shared：shared 在 contracts 之下，不能引用 `TradePlan`
- 放风控或执行模块：提交是一整个流程（风控、预留、确认、下单），不是哪个模块自己的用例
- 两个 `Callable` 类型：两者总是同一方实现、一起注入，一个协议少一个参数

返回 `RiskDecision` 而不是新类型：适配器回复要的正是结论、最终股数和不通过的规则；被拒绝时没有确认消息，原因只能在回复里写全。

### D6 减仓和清仓的板块

`TradePlan` 四种动作都要板块，但减仓和清仓的板块对人没有意义：持仓的板块取自开仓订单，卖单的板块不参与计算。适配器先 `held_sector(symbol)`，有就用，没有就回复「没有持仓」、不提交；查询抛错就回复「查持仓出错」、不提交。

- 「没有持仓」顺手挡住了明显的错单；风控本来也会以「可卖数量」拒绝它，只是要先走一遍提交
- 「下单流程」实现 `held_sector` 时用账户数据里的持仓（`HeldPosition.sector`），和风控看到的是同一份

考虑过：

- `TradePlan` 的板块改成退出方案不填：要改风控、执行（订单的板块）、紧急退出（`close_plan`）、确认消息和大量测试数据，超出本变更
- 由提交一方补板块：`submit` 就不能收 `TradePlan`，要另定一个「没有板块的方案」类型
- 填一个占位的板块：订单记录和确认消息里会出现假的板块
- 让人填：没有意义，填错了也没有影响，只是多打字

### D7 最小变动单位放在 TradePlan

```python
_ONE_DOLLAR = Decimal(1)
_CENT = Decimal("0.01")
_SUB_DOLLAR_TICK = Decimal("0.0001")
_PRICE_NAMES = {"limit_price": "限价", "stop_price": "止损价"}


class TradePlan(BaseModel):
    # 字段和 _check_fields_for_action 不变

    @field_validator("limit_price", "stop_price")
    @classmethod
    def _check_tick(cls, price: Decimal | None, info: ValidationInfo) -> Decimal | None:
        """1 美元及以上只能到 0.01，低于 1 美元只能到 0.0001。"""
```

- 按数值判断：`price % tick == 0`，所以 `180.500` 符合、`180.505` 不符合；字段约束 `gt=0` 先于它检查，到这里价格一定大于 0
- 用字段校验器而不是放进 `_check_fields_for_action`：限价和止损价都不对时两条原因都报出来，适配器一次列全
- 错误信息写中文字段名和填的价格，比如「限价 180.505 不符合最小变动单位：1 美元及以上最多 2 位小数，1 美元以下最多 4 位小数」
- 这是美股报价的规则（Reg NMS Rule 612），不是可以调的阈值，所以写成常量，不进配置；moomoo 实盘也一样

放在 contracts，而不是：

- 只在适配器里检查：以后模型给出的方案就没人管，到券商那里才被拒，止损单永远挂不上
- 作为一条风控规则：风控规则要「股数越小越容易通过」，价格写错和股数无关；格式错误本来就在构造方案时报
- 下单前按最小变动单位取整：add-alpaca-paper-broker 的 design 已经否决，取整以后券商的止损价和止损目标对不上，会反复改单；而且人确认过的价格被悄悄改了

现有的测试数据和 Hypothesis 策略里的价格都是两位小数（`places=2`），不受影响；实现时跑全部测试确认。

### D8 适配器的结构

```python
@dataclass(frozen=True, slots=True)
class PlanEntry:
    """一条 /plan 里人填的内容：Discord 给的原样文字和整数，没填的是 None。"""

    action: TradeAction
    symbol: str
    sector: str | None = None
    quantity: int | None = None
    limit: str | None = None
    stop: str | None = None
    emergency: str | None = None
    deviation: str | None = None
    expires: str | None = None


class PlanFormatError(ValueError):
    """填的内容构不成方案。reasons 是全部原因，每条写明字段和填的文字。"""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("；".join(reasons))
        self.reasons = tuple(reasons)


def build_plan(
    entry: PlanEntry, plan_id: UUID, sector: str, calendar: TradingCalendar
) -> TradePlan:
    """逐个字段解析，再构造方案；不通过时抛 PlanFormatError，一次列出全部原因。"""
```

```python
class CommandCall(Protocol):
    """一次 /plan：discord.Interaction 里适配器用到的部分。回复都只有本人看得到。"""

    @property
    def user_id(self) -> int: ...

    async def refuse(self, text: str) -> None:
        """立即回复，这就是应答；用来拒绝别人。"""
        ...

    async def defer(self) -> None:
        """延迟应答：Discord 只对本人显示「正在思考」。"""
        ...

    async def reply(self, text: str) -> None:
        """把「正在思考」换成这段文字；应答后 15 分钟内有效。"""
        ...


class PlanCommand:
    """处理 /plan，见 manual-plans spec。"""

    def __init__(
        self,
        intake: PlanIntake,
        ids: IdGenerator,
        calendar: TradingCalendar,
        config: DiscordConfig,
    ) -> None: ...

    async def handle(self, call: CommandCall, entry: PlanEntry) -> None:
        """在事件循环上运行；查持仓、解析和提交放到工作线程里。"""
```

```python
class PlanGroup(app_commands.Group):
    """/plan 的四个子命令：只声明这个动作允许的字段，必填项由 Discord 检查。"""

    def __init__(self, command: PlanCommand) -> None:
        super().__init__(name="plan", description="手工录入交易方案")
        self._command = command

    @app_commands.command(name="buy", description="买入：新建仓，限价单")
    @app_commands.describe(**OPENING_FIELDS)
    async def buy(
        self,
        interaction: discord.Interaction,
        symbol: str,
        sector: str,
        quantity: app_commands.Range[int, 1],
        limit: str,
        stop: str,
        emergency: str | None = None,
        deviation: str | None = None,
        expires: str | None = None,
    ) -> None:
        entry = PlanEntry(
            TradeAction.BUY, symbol, sector, quantity, limit, stop, emergency, deviation, expires
        )
        await self._command.handle(InteractionCall(interaction), entry)

    # add 和 buy 的字段相同；reduce、close 只有各自的字段
```

- `plans.py` 只有不做 I/O 的函数：解析、构造方案、每种回复的文字，单独测试
- `PlanCommand` 放全部流程（是不是本人、应答、取 ID、线程、超时、回复、日志），只依赖 `CommandCall`、`PlanIntake`、`IdGenerator`：测试用替身就能覆盖。和 `DiscordNotifier` 只依赖 `Gateway`、`ButtonPress` 是同一个做法
- `gateway.py` 只翻译：`PlanGroup` 把 discord.py 解析好的参数装成 `PlanEntry`，`InteractionCall` 把 `CommandCall` 的三个方法映射到 `response.send_message(ephemeral=True)`、`response.defer(ephemeral=True)`、`edit_original_response(content=...)`
- `DiscordGateway(config, plans)`：`_Client` 构造时建 `CommandTree`，把 `PlanGroup(plans)` 加到配置的服务器下；`run` 在 `login` 之后、`connect` 之前 `tree.sync(guild=...)`。注册的错误转成 `NotificationUnavailableError("注册斜杠命令失败：…")`，`run` 抛出，`DiscordNotifier.start()` 按已有的路径报启动失败
- `plans` 是必填参数：正式运行总要注册 `/plan`；add-discord-notify 的测试和冒烟测试跟着传一个用替身提交接口的 `PlanCommand`
- `_Client.on_interaction` 不变：只把带消息的按钮交给 `DiscordNotifier`，命令交互由 `CommandTree` 交给 `PlanGroup`。两边各管各的，spec「和确认按钮互不干扰」由这个分流保证

考虑过：

- 在 `on_interaction` 里自己解析命令的原始数据、用 HTTP 注册：要自己拼命令定义和选项类型，`CommandTree` 已经做了，而且做得对
- `Gateway.run` 多一个参数、由 `DiscordNotifier` 转交命令：`DiscordNotifier` 和它已经测好的线程逻辑都要改，而它和录入方案无关
- 注册失败时只写日志、照常启动：`/plan` 用不了，M2 就没有方案来源，不如启动时就报出来；部署时授权一次就好

### D9 应答、线程和超时

```
discord 线程（事件循环）                              工作线程（asyncio.to_thread）
/plan ─▶ CommandTree ─▶ PlanGroup ─▶ PlanCommand.handle
           不是本人 ─▶ refuse（应答），写警告日志，结束
           defer（应答，只对本人显示「正在思考」）
           new_id()
           wait_for(to_thread(_process), 提交超时) ──▶ held_sector（减仓、清仓）
                                                     build_plan
                                                     submit ─▶ 以后是下单流程
                                                     写日志，返回回复的文字
           ◀─────────────────────────────────────────
           reply（换掉「正在思考」）
```

- 应答之前只比较用户 ID，然后 `defer`：解析虽然不做 I/O，但减仓和清仓要先查持仓，统一放在应答之后，只有一条路径
- `_process` 是同步函数，包括查持仓、解析、提交和写结果日志，从不抛错：每种结果都变成回复的文字。所以 `wait_for` 超时以后它在工作线程里接着跑完，结果照样写进日志（spec「提交超时」的 70 秒那条日志）
- `asyncio.wait_for` 超时只取消等待，工作线程里的提交不受影响；不能也不该中止它：提交可能已经启动了工作流
- 延迟应答失败（`defer` 抛错）：写错误日志，不取 ID、不提交。Discord 会告诉人命令失败，他再运行一次，不会多出一个没人知道的方案
- 回复失败（`reply` 抛错）：写错误日志，带方案 ID 和回复的文字；方案已经提交的，确认消息照常发到频道
- 拒绝别人的提示发不出去：写日志
- 提交超时放在 `DiscordConfig.submit_timeout`（默认 60 秒，大于 0、小于 15 分钟）：它是适配器等待的上限，和 `send_timeout` 同类；15 分钟是 Discord 接受回复的期限

### D10 回复的文字

一条风控通过的回复：

```
风控通过：买入 AAPL 100 股
请在 <#2002> 的确认消息上确认
方案 ID 00000000-0000-0000-0000-000000000001
```

调整、拒绝、出错多出的部分：

```
风控调整：加仓 MSFT 200 股 → 120 股，原因见确认消息

风控拒绝：买入 AAPL 100 股，不会下单
- 降级模式：系统处于降级模式，暂停开仓

提交出错：数据库暂时连不上
方案 ID … 可能已经提交：频道里出现这个方案的确认消息就照常确认或拒绝，没有出现再重新录入
```

- 第一行是结论：Discord 的「只有你能看到」消息就显示在输入框上方，一眼能看到
- 通过和调整都请人到频道里确认，频道用 `<#频道 ID>`，点一下就跳过去；调整的原因只在确认消息里写一遍
- 拒绝时没有确认消息，规则在回复里写全
- 股数：通过和调整用风控的最终股数（清仓也有）；拒绝时用方案的股数，清仓不写

### D11 测试

- `tests/fakes/ids.py`：`FakeIds`，依次给出 `UUID(int=1)`、`UUID(int=2)`……，记下给了几个
- `tests/fakes/plan_intake.py`：`FakePlanIntake`，满足 `PlanIntake`。按给定的映射回答 `held_sector`；`submit` 记下收到的方案和运行的线程，默认返回通过（最终股数是方案的股数，清仓是 100），可以换成别的结果、抛错，或者等一个 `threading.Event` 再返回；查持仓也可以安排抛错
- `tests/contracts/test_trade_plan.py`：risk-rules delta 的四个新场景
- `tests/integrations/test_random_ids.py`：满足 `IdGenerator`；1000 个都是版本 4、没有重复
- `tests/integrations/discord/test_plans.py`：spec「字段的解析」的全部场景（用 `build_plan` 和样例日历），「减仓和清仓的板块」里板块的使用，每种回复的文字（「回复」的场景）
- `tests/integrations/discord/test_config.py`：提交超时的默认值和不合法的值
- `tests/integrations/discord/calls.py`：`FakeCall` 满足 `CommandCall`，把 `refuse`、`defer`、`reply` 按顺序记进一个可以和 `FakePlanIntake` 共用的记录，用来检查先后；可以安排 `defer` 或 `reply` 抛错
- `tests/integrations/discord/test_commands.py`：`PlanCommand` 的流程。用 `asyncio.run` 运行 `handle`；「只接受本人」「应答和处理的先后」「减仓和清仓的板块」「方案 ID」「提交接口」「回复发不出去」「日志」的场景。提交超时配成 0.2 秒；「提交期间照常处理别的交互」用 `threading.Event` 让提交停住，在同一个事件循环上让别人的命令和另一个协程跑完，再放行
- `tests/integrations/discord/test_gateway.py`：`PlanGroup` 的四个子命令的字段、必填、类型和最小值（spec「子命令和字段」）；注册的错误转换（`Forbidden`、`HTTPException` 转成通知不可用，信息里有「注册斜杠命令失败」和 Discord 给的原因）；`DiscordGateway(config, plans)` 满足 `Gateway`
- 每个 spec 场景一个测试，名字或说明里写明场景；只能在真实 Discord 上验证的（注册、全局命令里没有、只有本人看得到、真实的 3 秒应答）写明由冒烟测试覆盖
- 冒烟测试 `tests/integrations/discord/test_plan_smoke.py`，沿用 `discord_bot` 标记，4 个环境变量缺一个就跳过：
  - 替身提交接口：SPY 有持仓，板块「测试」；买入 SPY 返回通过，减仓 SPY 返回拒绝（规则「冒烟测试：故意拒绝」），别的都不会提交到
  - 启动，用 REST 读服务器命令：只有一个 `plan`，四个子命令；全局命令里没有 `plan`。关掉再启动一次，仍然只有一个
  - 推送一条告警，请人在 5 分钟内依次运行四条命令：填全了的 `/plan buy symbol:spy …`（风控通过）、限价 `400.005` 的 `/plan add`（格式错误）、`/plan reduce symbol:SPY quantity:1`（风控拒绝）、`/plan close symbol:XYZ`（没有持仓）
  - 等到日志里有这四条命令的处理结果，核对替身收到的两个方案的每个字段（包括 0.08 和 0.05 这样的换算），没有「回复没有发出去」的错误日志，日志里没有 token
  - 回复是不是只有本人看得到、文字对不对，由人在 Discord 里看
- 沿用 `discord_bot` 而不是新标记：都是连真实机器人、需要人操作的测试；只跑这一组时指定文件 `uv run pytest -m discord_bot tests/integrations/discord/test_plan_smoke.py`

## Risks / Trade-offs

- [提交出错或超时以后人重新录入，出现两个方案] → 回复提示先看频道里有没有这个方案 ID 的确认消息；两个方案都要人工确认，不会悄悄下两次单
- [手工录入不经过 PM 和 Critic] → 只有配置的用户能录入，照样过风控和人工确认；它是模型接入之前的工具
- [Discord 客户端的必填和最小值检查可能被绕过（直接调 API）] → `TradePlan` 的校验照样挡住，回复格式错误
- [注册失败会让整个启动失败，告警也发不出去] → 启动日志写明原因；「部署」授权 `applications.commands` 一次就好
- [同一个 token 在本机和部署机器上同时运行：两处都注册，命令可能被另一个进程收走] → 和按钮一样（add-discord-notify 的风险），冒烟测试前先停掉部署机器上的程序
- [discord.py 的 `app_commands` 更新较快] → 版本下限是读过源码的 2.7.1；冒烟测试覆盖注册、应答和回复
- [Discord 限制覆盖注册服务器命令每 30 秒 2 次（冒烟测试时实测）：额度用完以后 discord.py 要等到额度恢复才继续连接，30 秒内第二次启动可能超过连接超时、启动失败] → 部署的重启间隔是 30 秒，每次启动只注册一次，碰不到；冒烟测试每次启动前等到距离上一次启动满 31 秒（`tests/integrations/discord/pacing.py`）

## Migration Plan

- 没有数据库迁移，没有新依赖
- 回滚：revert 代码；服务器里留着的 `/plan` 运行时 Discord 提示没有响应，下次注册时覆盖
