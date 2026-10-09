# Design

## Context

现状：

- 项目里没有任何通知的通道，也还没有用过 `logging`。M1 和 M2 已归档的变更把告警（无保护仓位、对账不一致、降级模式、紧急退出执行后）和人工确认（确认、拒绝、超时时释放预留）都留给了 M2 的流程变更
- 风控给出 `RiskDecision`（contracts）：结论、最终股数、生效的紧急阈值和有效期、不通过的规则（`RiskViolation` 的 `rule` 和 `message`，调整时是请求股数下不通过的规则）。清仓方案不写股数，`RiskDecision.quantity` 是风控算出的全部可卖股数
- 执行模块、M1 的 service 和 M2 的 DBOS 工作流都是同步的；DBOS 用持久化通知（`DBOS.send` / `DBOS.recv`）等人工确认，`recv` 带超时
- workflows 和 integrations 互相不能 import；integrations 实现 modules 定义的端口，由 bootstrap 注入

discord.py（2026-10-08 读 2.7.1 的源码）：

- 自带类型标注（py.typed），依赖 aiohttp；全部 API 是 asyncio 的，`Client.start(token)` = `login` + `connect`，`connect(reconnect=True)` 在网络中断后自己重连；token 无效时 `login` 抛 `LoginFailure`
- 发消息只走 HTTP（`get_partial_messageable(channel_id).send(...)` 不需要缓存）；按钮的交互从网关（WebSocket）推过来，事件 `on_interaction` 收到全部交互
- 交互必须在 3 秒内应答（`response.defer()`、`response.send_message(..., ephemeral=True)` 或 `response.edit_message(...)`），应答以后 15 分钟内可以 `edit_original_response` 改按钮所在的消息、用 `followup.send(..., ephemeral=True)` 补一条仅自己可见的回复
- 按钮的 `custom_id` 最长 100 字，按下时原样交回；`View(timeout=None)` 不在构造时要求运行中的事件循环
- 消息内容最长 2000 字；`AllowedMentions` 控制谁会被通知；嵌入（embed）可以带颜色和时刻，编辑消息不会再次通知
- 日志：DEBUG 级别会记下请求的地址，交互回调的地址里有那次交互自己的短期 token；机器人的 token 只放在请求头里，不进日志

要求见 specs/discord-notification/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
modules/notification/            (新，对应架构图「风控与执行」里的「人工确认」和「通知与人工确认」)
  __init__.py   导出 ConfirmationOutcome、ConfirmationRequest、PendingConfirmation、
                DecisionHandler、Notifier、NotificationUnavailableError、
                NotificationConfig、NotificationService
  domain/confirmation.py   ConfirmationOutcome、ConfirmationRequest、PendingConfirmation
  ports.py      Notifier、DecisionHandler、NotificationUnavailableError
  config.py     NotificationConfig
  service.py    NotificationService

integrations/discord/            (新)
  __init__.py   导出 DiscordConfig、DiscordGateway、DiscordNotifier（add-bootstrap-wiring 加上 Gateway：装配要用它）
  config.py     DiscordConfig（含 from_env）
  messages.py   消息内容、按钮、结果的嵌入、按钮 custom_id 的编码和解析（不做 I/O）
  notifier.py   Gateway、ButtonPress（适配器内部的协议）、DiscordNotifier
  gateway.py    DiscordGateway（discord.py 的 Client）、InteractionPress、错误的转换
                （add-manual-plan-entry 在这里加上 /plan 的定义和注册，见它的 design D8）
```

- 通知模块只依赖 contracts（`TradePlan`、`RiskDecision`）和 shared（`Clock`），不依赖别的模块
- integrations/discord 只依赖通知模块的包根、contracts 和 shared
- 模块之间传递的 Pydantic 模型：只用已有的 `TradePlan` 和 `RiskDecision`，不新增、也不修改 contracts。通知模块自己的类型（确认请求、结果、发出的确认）是 domain 里的 frozen dataclass 和 StrEnum，从包根导出，和行情模块的 `Quote` 一样：它们只在「工作流 → 通知模块 → 它的端口实现」和「适配器 → 回调 → 工作流」之间走，不在两个业务模块之间传递
- `DiscordConfig` 是适配器的配置，用 Pydantic 校验，和 `AlpacaConfig`、`MoomooConfig` 一样放在 integrations 里
- 纯区域的白名单不变：domain 只 import `dataclasses`、`datetime`、`enum` 和 contracts；config 只用 pydantic 和 `datetime`
- 不改风控、执行和其他模块

## Goals / Non-Goals

**Goals:**

- 工作流只依赖通知模块，不知道 Discord；以后换通道只写新适配器
- 同步的调用方不碰 asyncio；任何调用都有等待上限，Discord 出问题时只报通知不可用
- 按钮在 3 秒内应答，回调再慢也不卡住 Discord 的事件处理
- 进程重启不让旧的确认消息失效，也不需要为它建表

**Non-Goals:**

按「编程风格」省略的情况：

- Discord 在启动那一刻连不上：启动失败，不在适配器里反复重试，由「启动与装配」和部署的自动重启处理
- 进程恰好在 Discord 收下确认消息、工作流还没记下这一步时崩溃：恢复后会再发一条，两条的按钮都指向同一个方案
- 告警不去重：调用方重试时可能发两条一样的告警
- 本人在截止前几毫秒按下、回调送达时工作流已经超时：工作流报告已超时，覆盖消息上的已确认
- 回调一直不返回（比如数据库卡住）：这条消息一直是「还在处理」，不另设超时
- 回调成功、但改消息失败：结果已经送达，消息上按钮还在，只写日志；再按会得到「已有结果」的提示
- 重启以后、按钮去掉之前又按一次：回调可能收到同一个方案两次，接收方只用第一条
- 确认消息超过 2000 字：不通过的规则最多十几条，远到不了，不截短
- 机器人运行中被踢出服务器或被收回权限：每次发送都报通知不可用并写日志，不另外检查
- 内存里记下的消息结果和请求不清理：每天只有几条
- 斜杠命令、别的按钮：本变更不应答，留给「手工录入方案」

## Decisions

### D1 新建通知模块，端口放在它里面

工作流（下单流程、紧急退出流程、收盘对账、健康监控接线）要推送告警和请求确认，但 workflows 不能 import integrations，所以要有一个模块定义端口，由 integrations/discord 实现、bootstrap 注入。新建 `modules/notification/`，对应架构图里的「人工确认」节点和「技术栈与部署」的「通知与人工确认」一行。

模块里放：

- 端口 `Notifier`：告警、请求确认、报告结果三个同步方法，失败一律抛 `NotificationUnavailableError`
- 回调 `DecisionHandler`：适配器收到按钮以后调用它。它是模块定义、bootstrap 实现、适配器调用的接口，所以也放在 ports
- 用例 `NotificationService`：算截止时刻、挡住被风控拒绝的方案，以及告警和报告结果失败时只写日志（D3）。工作流用它，不直接用端口，和行情、风控等模块的用法一致
- 配置 `NotificationConfig`：确认超时

考虑过：

- 端口放进执行模块或风控模块：告警的调用方遍布各个流程，确认也不是下单或风控的职责
- 端口放进 `shared`：shared 是纯区域，只放基础类型和协议；外部系统的接口按约定属于某个模块的 ports（和 add-moomoo-quotes 的 D1 同理）
- 告警和确认拆成两个端口：两者走同一个机器人、同一个频道，由同一个类实现；Python 的 Protocol 是结构类型，以后哪个工作流只想要告警，自己写一个窄的 Protocol 就行

### D2 通知模块的类型和端口

```python
class ConfirmationOutcome(StrEnum):
    """一个确认的结果，见 spec「确认请求和截止时刻」的表。"""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    VOIDED = "voided"


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    """请求确认的一个方案。deadline 是 UTC 时刻，由 NotificationService 算出。"""

    plan: TradePlan
    decision: RiskDecision
    deadline: datetime


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    """发出的确认消息：message_id 由通知通道给出，调用方报告结果时原样传回。"""

    message_id: str
    deadline: datetime
```

```python
class NotificationUnavailableError(RuntimeError):
    """通知通道超时、断线、报错，或者还没启动、已经关闭：这次没有发出去。"""


type DecisionHandler = Callable[[UUID, ConfirmationOutcome], None]
"""人按下按钮以后的回调：方案 ID 和 CONFIRMED 或 REJECTED。

在适配器的工作线程里调用，可以阻塞；抛错表示没有送达，适配器会请人再按一次。
"""


class Notifier(Protocol):
    """通知通道。三个方法都可以从任何线程同步调用，失败时抛 NotificationUnavailableError。"""

    def alert(self, text: str) -> None:
        """推送一条告警。"""
        ...

    def request_confirmation(self, request: ConfirmationRequest) -> PendingConfirmation:
        """发出确认消息；同一个方案重复请求时返回第一次的那条。"""
        ...

    def finish_confirmation(self, message_id: str, outcome: ConfirmationOutcome) -> None:
        """把确认消息改为这个结果、去掉按钮；消息已被删掉时什么也不做。"""
        ...
```

- 结果只用一个枚举：回调只会收到 CONFIRMED 或 REJECTED，调用方报告的是 TIMED_OUT 或 VOIDED，消息上显示的是四种之一。分成两个枚举，消息上的显示又要合回一个
- `message_id` 用字符串：端口不暴露 Discord 的整数 ID 类型；工作流把 `PendingConfirmation` 作为 DBOS step 的输出保存下来，重启以后照样能报告结果（D6）
- `PendingConfirmation` 带回截止时刻：重复请求时返回的是第一次那条消息上的截止时刻（D7），工作流按它等，就和消息一致

### D3 用例和确认超时

```python
class NotificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 请求确认以后多久没有结果算超时
    confirmation_timeout: timedelta = Field(default=timedelta(minutes=30), gt=timedelta(0))


class NotificationService:
    def __init__(self, notifier: Notifier, clock: Clock, config: NotificationConfig) -> None: ...

    def alert(self, text: str) -> None:
        """推送告警；发不出去时写错误日志（含文字和原因），不抛错。"""

    def request_confirmation(self, plan: TradePlan, decision: RiskDecision) -> PendingConfirmation:
        """截止时刻 = 现在 + 确认超时。风控拒绝的方案抛 ValueError；发不出去时抛
        NotificationUnavailableError。"""

    def finish_confirmation(self, message_id: str, outcome: ConfirmationOutcome) -> None:
        """报告已超时或已作废；发不出去时写错误日志，不抛错。"""
```

超时由谁执行：

- 等确认的是以后「下单流程」的 DBOS 工作流：请求确认这一步返回以后，`DBOS.recv(topic, timeout_seconds=confirmation_timeout)`。`recv` 的超时是持久化的，进程重启以后接着算
- 本变更只给出超时的值、把截止时刻写进消息和按钮，并在截止以后拒收按钮。工作流开始等的时刻晚于算截止时刻的时刻，所以工作流的截止不早于消息上的截止：适配器收下的按钮，一定在工作流超时之前送到（只差回调那几毫秒，见省略清单）
- 工作流等不到时调用 `finish_confirmation(..., TIMED_OUT)`，消息改为已超时

考虑过：

- 适配器自己计时，到点改消息、通知工作流：要在适配器里保存定时任务，重启以后要重建；工作流本来就要等，而且 DBOS 的超时是持久化的
- 截止时刻由工作流算好传进来：每个工作流都要读时钟、读配置；放在用例里只有一处

告警和报告结果失败时不抛错、请求确认失败时抛错，见 proposal「需要人决定」第 1 条：告警是尽力而为，调用方不该因为发不出告警而中断对账或紧急退出；报告结果失败时消息上按钮还在，但过了截止时刻再按只会显示已超时。请求确认失败时，调用方要知道这个方案没人能确认。

用例第一次在项目里用 `logging`：`logging.getLogger(__name__)`，不配置 handler，由「启动与装配」配置；M4 换成程序日志。

### D4 Discord 配置和环境变量

```python
class DiscordConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: SecretStr
    guild_id: int = Field(gt=0)
    channel_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    # 启动时等多久连上 Discord；每次同步调用最多等多久
    connect_timeout: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
    send_timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """从 DISCORD_BOT_TOKEN、DISCORD_GUILD_ID、DISCORD_CHANNEL_ID、DISCORD_USER_ID 读取。"""
```

- 变量名带 `DISCORD_` 前缀，和 Alpaca 的 `APCA_` 一样按来源分组；`BOT_TOKEN` 写明这是机器人的 token，不是用户的
- `from_env` 放在适配器里：变量名只在一处定义，「启动与装配」和冒烟测试都调用它。参数是映射而不是直接读 `os.environ`，测试不用改进程的环境变量
- 缺少（没有或为空）的变量一次全部列出，免得改一个、跑一次、再报下一个；ID 不是正整数时报 `ValueError`，写变量名，不写值以外的东西
- token 用 `SecretStr`：`repr`、`str` 和 Pydantic 的错误信息里都是 `**********`；只在登录时取出
- 两个超时不放环境变量：默认值够用，要改时由「启动与装配」传

服务器 ID 本变更用来在启动时核对频道属于这个服务器，防止配错频道；「手工录入方案」用它注册服务器内的斜杠命令（立即生效，全局命令要等最多一小时）。

### D5 消息内容

`messages.py` 只有不做 I/O 的函数，单独测试：

```python
def alert_content(text: str, user_id: int) -> str: ...


def confirmation_content(request: ConfirmationRequest, user_id: int) -> str: ...


def confirmation_view(request: ConfirmationRequest) -> discord.ui.View: ...


def outcome_embed(outcome: ConfirmationOutcome, at: datetime) -> discord.Embed: ...


def only_user(user_id: int) -> discord.AllowedMentions: ...
```

P1（spec 的基准数据）的确认消息：

```
<@1001> 请确认：买入 AAPL 100 股
限价 180.50，金额 18,050.00；止损价 171.00；紧急阈值 8%
科技板块；有效期至 <t:1791489600:f>
请在 <t:1791469800:t>（<t:1791469800:R>）前确认，过时按超时处理
方案 ID 0b7c2d4e-3f5a-4b6c-8d9e-0f1a2b3c4d5e
```

P2（被风控调整）多出：

```
已被风控调整：200 股 → 120 股
- 单笔计划风险：MSFT 的计划风险将是 3,040.00，超过上限 2,000.00
- 组合合计风险：组合合计风险将是 9,000.00，超过上限 8,000.00
```

- 第一行最重要：手机推送只显示开头，所以动作、股票、最终股数都放在第一行
- 股数用 `RiskDecision.quantity`（风控的最终股数），调整时另写方案原来的股数；清仓的方案没写股数，「已被风控调整」那行只写调整后的股数
- 价格照方案里的 Decimal 原样写；金额 = 最终股数 × 限价，`f"{amount:,.2f}"`；紧急阈值和允许价格偏离写成百分数，去掉末尾的 0（0.08 是 8%，0.075 是 7.5%）
- 时刻用 Discord 的时间戳标记 `<t:秒数:格式>`：每个人按自己设备的时区看到，不用在代码里选时区；`:R` 显示「还有 25 分钟」这样的相对时间
- 告警：`<@用户> ` 加文字；整条超过 2000 字时把文字截短，最后一个字换成「…」
- 发送时一律带 `only_user(user_id)`（`AllowedMentions(everyone=False, roles=False, users=[用户])`）：告警文字可能带异常信息，里面的 @everyone 或别人的 @ 不会通知任何人
- 按钮：「确认」绿色（success），「拒绝」红色（danger）；`View(timeout=None)`
- 结果：嵌入的标题是已确认（绿）、已拒绝（红）、已超时或已作废（灰），时刻是得出结果的时刻。用嵌入而不是改正文：编辑时只换嵌入、去掉按钮，正文不动，不用记住原来的正文；多次改结果也只是换掉嵌入

### D6 按钮自带状态

按钮的 `custom_id` 是 `confirmation:<选择>:<方案 ID>:<截止时刻的秒数>`，比如：

```
confirmation:confirmed:0b7c2d4e-3f5a-4b6c-8d9e-0f1a2b3c4d5e:1791469800
```

一共 70 字，低于 100 字的上限。

- 按下时只看 `custom_id` 就知道方案、选择和截止时刻，不需要内存里的状态，也不需要建表：进程重启以后旧消息的按钮照样能用。截止时刻按秒向下取整，最多早不到 1 秒拒收
- 前缀 `confirmation:` 留给本变更；解析不了的交互（别的按钮、斜杠命令）不应答，留给以后的变更
- 按钮由 `on_interaction` 统一处理，不用 discord.py 的 View 回调：View 回调要求进程里有同一个 View 对象，重启以后就没了

考虑过：

- discord.py 的 `DynamicItem`（按正则解析 custom_id 的按钮）：效果相同，但要为确认和拒绝各写一个类、启动时登记；`on_interaction` 加一个解析函数更直接，也更好测
- 把截止时刻存在内存里、按消息 ID 查：重启以后就没了；从消息创建时刻加确认超时推算：确认超时改了以后，旧消息算错

### D7 适配器的结构和线程模型

```python
class ButtonPress(Protocol):
    """一次按钮按下：discord.Interaction 里适配器用到的部分。"""

    @property
    def user_id(self) -> int: ...

    @property
    def custom_id(self) -> str: ...

    @property
    def message_id(self) -> int: ...

    async def defer(self) -> None:
        """应答 Discord，暂不改消息。"""
        ...

    async def reply(self, text: str) -> None:
        """只有按的人看得到的回复；还没应答时，这就是应答。"""
        ...

    async def show(self, embed: discord.Embed) -> None:
        """去掉按钮、换上结果；还没应答时，这就是应答。"""
        ...


type PressHandler = Callable[[ButtonPress], Coroutine[Any, Any, None]]


class Gateway(Protocol):
    """适配器用到的 Discord 连接：DiscordGateway 是真实的实现，测试用替身。"""

    async def run(self, on_press: PressHandler, ready: Callable[[], None]) -> None:
        """登录、连接、核对频道，然后调用 ready；一直运行到 close。

        失败时抛 NotificationUnavailableError。"""
        ...

    async def send(
        self,
        content: str,
        view: discord.ui.View | None,
        mentions: discord.AllowedMentions,
    ) -> int:
        """往频道发一条消息，只通知 mentions 里允许的人，返回消息 ID。"""
        ...

    async def edit(self, message_id: int, embed: discord.Embed) -> None:
        """去掉这条消息的按钮、换上结果；消息已被删掉时什么也不做。"""
        ...

    async def close(self) -> None: ...


class DiscordNotifier:
    """用 Discord 实现 Notifier。同步方法可以从任何线程调用。"""

    def __init__(
        self,
        gateway: Gateway,
        config: DiscordConfig,
        clock: Clock,
        on_decision: DecisionHandler,
    ) -> None: ...

    def start(self) -> None: ...

    def close(self) -> None: ...
```

`DiscordNotifier` 放全部逻辑（线程、同步调用、按钮的处理、去重），`DiscordGateway` 只把 discord.py 的对象翻译成上面两个协议：测试用替身 Gateway 和替身按钮就能覆盖全部逻辑，真实的 Gateway 由冒烟测试覆盖。和 moomoo 适配器只依赖 `SnapshotContext` 是同一个做法。

线程：

```
调用方线程（DBOS step 等）      discord 线程（事件循环）                   工作线程（asyncio.to_thread）
alert() ──run_coroutine_threadsafe──▶ gateway.send ──▶ Discord
       ◀──future.result(≤ 发送超时)──
                                 on_interaction ─▶ 内存里判断 ─▶ defer（应答）
                                                   └─ to_thread(on_decision) ──▶ 回调（以后是 DBOS.send）
                                                   ◀──────────────────────────
                                                   show（去掉按钮、显示结果）
```

- `start()` 起一个 daemon 线程 `discord`，在里面 `asyncio.run(...)` 运行 `gateway.run`；用 `threading.Event` 等 `ready`，最多等连接超时。`run` 先抛错（登录失败、频道不对）时，把它作为启动失败抛出；超时则关掉连接、报连接超时。daemon 线程保证忘了 `close()` 也能退出进程
- 同步方法：`asyncio.run_coroutine_threadsafe(协程, loop)`，再 `future.result(timeout=发送超时)`。超时就 `future.cancel()` 并报通知不可用。没启动、已关闭或线程已经结束时直接报通知不可用，不等
- `run` 意外结束（比如 token 被重置）：线程记下原因、写错误日志，之后的调用立即报连接已中止
- 按钮：`on_interaction` 在事件循环上运行，应答之前只做内存里的判断（解析 custom_id、比用户、查结果和处理中、比截止时刻），然后 `defer()`；回调用 `asyncio.to_thread` 放到工作线程里，回调再慢，事件循环照样处理别的交互和发送。回调成功以后用 `show` 改消息（`edit_original_response`，应答后 15 分钟内有效）
- 内存里的状态（每条消息的结果、处理中的消息、每个方案的确认请求）只在事件循环上读写，不需要锁
- 截止时刻的比较用注入的 `Clock`

按下的处理按 spec「按钮的处理」的表依次判断。调用方报告结果（`finish_confirmation`）时先记下结果再改消息；回调返回以后发现这条消息已经有调用方报告的结果，就不再改，保证调用方的结果是最后显示的那个。

重复请求同一个方案：`dict[UUID, asyncio.Task[PendingConfirmation]]`。已有这个方案的任务、而且没有失败时，`await asyncio.shield(task)` 等它的结果；否则新建任务发送。`shield` 让调用方超时取消时不取消发送本身，所以重试拿到的是那条迟到的消息。

考虑过：

- discord.py 跑在主线程、业务代码挪到别的线程：主线程归 bootstrap 和 DBOS
- 每次调用临时建一个事件循环、用 HTTP 发消息：发得出去，但收不到按钮，按钮要一直连着网关
- 按下以后先改消息、再调用回调：回调失败时消息已经显示已确认，工作流却没收到

### D8 DiscordGateway

- `run`：用 `discord.Intents(guilds=True)` 建 Client（不需要特权 intent），`login(token)`（`LoginFailure` 转成「Discord 登录失败」），后台 `connect(reconnect=True)`，`wait_until_ready()`；然后 `fetch_channel(channel_id)`，核对它是服务器 `guild_id` 里的文字频道，否则报错；最后调用 `ready` 并等连接结束
- `send`：`client.get_partial_messageable(channel_id).send(content, view=view, allowed_mentions=mentions)`，返回消息 ID。`mentions` 由 `DiscordNotifier` 每次传入 `only_user(user_id)`，替身 Gateway 记下它，单元测试就能检查
- `edit`：`get_partial_message(message_id).edit(embed=embed, view=None)`；`discord.NotFound` 时什么也不做
- 错误转换：`discord.HTTPException`（包括限流重试用完、服务器错误、没有权限）、`aiohttp.ClientError`、`OSError` 转成 `NotificationUnavailableError`，信息是状态码和 Discord 给的原因，不含 token
- `InteractionPress`：`defer` 是 `response.defer()`；`reply` 在还没应答时用 `response.send_message(text, ephemeral=True)`，已应答时用 `followup.send(text, ephemeral=True)`；`show` 在还没应答时用 `response.edit_message(embed=..., view=None)`，已应答时用 `edit_original_response(embed=..., view=None)`
- `on_interaction` 只把带消息的按钮交互交给 `on_press`；`DiscordNotifier` 的 `on_press` 自己捕获并记录处理中的异常（比如应答时 Discord 报错），不会中断连接
- add-manual-plan-entry 以后：构造改为 `DiscordGateway(config, plans)`，`run` 在 `login` 之后、`connect` 之前在配置的服务器注册 `/plan`，注册失败时 `run` 抛 `NotificationUnavailableError`；`/plan` 的交互由 discord.py 的命令树交给 `PlanCommand`，`on_interaction` 不变。见 add-manual-plan-entry 的 design D8

### D9 新增依赖和配置

- `uv add "discord.py>=2.7.1"`：当前最新的稳定版（2026-03 发布）。它带进 aiohttp，只在 `integrations/discord` 里用
- mypy：discord.py 自带类型标注，配置不用改
- pytest：登记标记 `discord_bot`（连真实 Discord 的冒烟测试），`addopts` 改为 `-m "not alpaca_paper and not moomoo_opend and not discord_bot"`
- Ruff 禁用了 `time`：测试里的等待用 `threading.Event().wait(秒)`，和 Alpaca 的冒烟测试一样

### D10 测试

- `tests/fakes/notifier.py`：`FakeNotifier`，满足 `Notifier`；记录告警、确认请求和报告的结果，按顺序给出消息 ID；可以安排下一次调用抛 `NotificationUnavailableError`
- `tests/modules/notification/`：`test_config.py`、`test_service.py`（`FakeNotifier` 和 `FakeClock`），覆盖「确认请求和截止时刻」，以及用例在告警、报告结果、请求确认失败时的处理（用 `caplog` 检查日志）
- `tests/integrations/discord/test_config.py`：「Discord 配置」的全部场景；token 用一个假值，检查 `repr`、`str` 和各种错误信息里都没有它
- `tests/integrations/discord/test_messages.py`：P1 到 P4 的确认消息、@ 和 `AllowedMentions`、告警截短、按钮的标签颜色和 custom_id、custom_id 的编码和解析（包括解析不了的）、四种结果的嵌入
- `tests/integrations/discord/gateway.py`：`FakeGateway` 满足 `Gateway`：记录发出和改过的消息；可以安排发送一直不返回、抛 `NotificationUnavailableError`、消息已被删掉、`run` 失败或一直不 ready、`run` 中途结束；`press(...)` 从测试线程把一次按下交给事件循环，等它处理完。`FakePress` 满足 `ButtonPress`，把每次调用按顺序记进一个和回调共用的记录里，用来检查「先应答再调用回调」
- `tests/integrations/discord/test_notifier.py`：「启动和关闭」「重复请求同一个方案」「按钮的处理」「结束确认」「同步调用和失败」的全部场景；回调记下自己运行的线程，检查不是事件循环所在的线程；用 `threading.Event` 让回调停住，检查这期间告警和别的按钮照常处理；「重启以后」用一个新的 `DiscordNotifier` 处理旧 custom_id 的按下；发送超时配成 0.2 秒，让超时的测试跑得快
- `tests/integrations/discord/test_gateway.py`：错误转换（`discord.NotFound` 在改消息时被忽略；`HTTPException`、`aiohttp.ClientError` 转成通知不可用，信息里有状态码）
- 每个 spec 场景一个测试，名字或说明里写明场景；`notifier: Notifier = DiscordNotifier(...)` 让 mypy 检查适配器满足端口
- 项目第一次用 `logging`：架构测试 `test_import_contracts.py` 调用的 import-linter 用 `logging.config.dictConfig` 配置自己的日志，会停用已经存在的全部 logger，之后的测试就收不到日志。那个测试跑完一个契约以后把原来启用的 logger 恢复
- 冒烟测试 `tests/integrations/discord/test_bot_smoke.py`，加 `discord_bot` 标记，4 个环境变量缺一个就跳过：
  - 无效 token：启动失败，原因是登录失败
  - 频道不对：把频道 ID 换成用户 ID 启动，启动失败，错误信息里有这个 ID
  - 自动部分：启动；推送一条带 @everyone 的告警，读回来 `mention_everyone` 是 false、`mentions` 只有配置的用户；请求确认一个 SPY 1 股的方案，用 httpx 直接读 Discord 的 REST 接口核对消息的正文、@ 和两个按钮；报告已作废，读回来按钮没了、嵌入是已作废；删掉一条确认消息后报告已超时，不报错
  - 人工部分：发一条确认消息和一条告警「请在 3 分钟内按上面那条消息的确认」，等回调收到确认、读回来显示已确认；再对拒绝做一遍
  - 整个过程用 `caplog` 在 DEBUG 级别收集全部日志，检查里面没有 token。先算出 `leaked = token in text` 再断言，失败时 pytest 只显示 `True`，不会把 token 打出来；读 REST 接口的请求头也不出现在断言里

## Risks / Trade-offs

- [Discord 断了，告警丢失] → 写错误日志；紧急退出和止损不依赖 Discord；开仓因为没人能确认自然停下。是否要备用通道，见 proposal「需要人决定」第 1 条
- [按钮在截止前几毫秒按下，回调送达时工作流已经超时] → 工作流报告已超时，覆盖已确认；结果是不下单，偏安全
- [一个进程同时只能有一个连接用这个 token 收交互：本机和部署机器同时运行时，按钮可能被另一个进程收走] → 「部署」写明同一个 token 只在一个地方运行；冒烟测试在本机跑时先停掉部署机器上的程序，或者用另一个测试用的机器人
- [回调在 discord 线程之外运行，以后的 `DBOS.send` 不在工作流里] → DBOS 允许在工作流之外调用 `send`；由「启动与装配」接上时验证
- [`DBOS.send` 发给已经结束的工作流也会成功：请求确认超过发送超时、工作流按超时结束，消息却晚到了，人随后按确认，回调成功、消息显示已确认，方案却永远不会下单] → 回调先确认这个方案的工作流还在等确认，不在等就抛错，按钮走「没有送达，请再按一次」，过了截止时刻再按显示已超时。适配器已经这样处理回调抛错，本变更不用改；判断和标记由「启动与装配」「下单流程」实现（proposal「对后续变更的影响」）
- [discord.py 和 Discord API 更新较快] → 版本下限是读过源码的 2.7.1；冒烟测试覆盖真实的应答、编辑和 @

## Migration Plan

- 新增依赖 discord.py；没有数据库迁移
- 回滚：revert 代码即可，其他代码还没有用到这两个包
