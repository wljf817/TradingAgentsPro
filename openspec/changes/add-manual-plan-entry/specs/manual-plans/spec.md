# Spec Delta

## Purpose

手工录入方案让人在模型接入之前，用 Discord 斜杠命令 `/plan` 录入交易方案：适配器把填的内容解析成交易方案、生成方案 ID，交给提交接口跑风控，再把格式错误和风控结果用只有本人看得到的回复告诉他；通过或调整的方案照常走人工确认。

## ADDED Requirements

### Requirement: 子命令和字段

`/plan` SHALL 有四个子命令，每个只有这个动作允许的字段。必填项由 Discord 标出，没填必填项时 Discord 不让发出命令；没有列出的字段在命令里填不了。

| 字段 | 含义 | `buy`（买入） | `add`（加仓） | `reduce`（减仓） | `close`（清仓） |
| --- | --- | --- | --- | --- | --- |
| `symbol` | 股票代码，文本 | 必填 | 必填 | 必填 | 必填 |
| `sector` | 板块，文本 | 必填 | 必填 | 无，取持仓的板块 | 无，取持仓的板块 |
| `quantity` | 股数，整数，最小 1（Discord 检查） | 必填 | 必填 | 必填 | 无，卖出全部可卖数量 |
| `limit` | 限价，文本 | 必填 | 必填 | 可选：填了是限价单，不填是市价单 | 同减仓 |
| `stop` | 止损价，文本 | 必填 | 必填 | 无 | 无 |
| `emergency` | 紧急阈值，百分数，文本 | 可选 | 可选 | 无 | 无 |
| `deviation` | 允许价格偏离，百分数，文本 | 可选 | 可选 | 无 | 无 |
| `expires` | 有效期：到这个交易日收盘，文本 | 可选 | 可选 | 可选 | 可选 |

买入和加仓 SHALL 是限价单。可选项不填时，方案里这一项不填，由风控用默认值（见 risk-rules spec 和 pre-order-recheck spec）。每个字段 SHALL 带一句中文说明，写明怎么填，比如「百分数，8 表示 8%」。

本 spec 的场景除非另有说明，都使用下面的基准数据：

- 配置的用户 ID 1001（「本人」），另一个用户 4004（「别人」）；频道 2002，属于服务器 3003
- ID 生成端口依次给出方案 ID U1、U2……（都是 UUID）
- AAPL 有持仓，板块「科技」；别的股票都没有持仓
- 提交接口没有另外说明时返回通过，最终股数是方案的股数（清仓是 100）
- 交易日历是纽交所日历；提交超时用默认值
- 命令写成 `/plan buy symbol:aapl quantity:100 …` 的形式，冒号后面是填的原样文字

#### Scenario: buy 的字段

- **WHEN** 读 `/plan buy` 的定义
- **THEN** 必填项依次是 symbol、sector、quantity、limit、stop，可选项是 emergency、deviation、expires
- **AND** quantity 是整数，最小值 1；其余都是文本

#### Scenario: add 的字段

- **WHEN** 读 `/plan add` 的定义
- **THEN** 字段和 `/plan buy` 相同

#### Scenario: reduce 的字段

- **WHEN** 读 `/plan reduce` 的定义
- **THEN** 必填项是 symbol、quantity，可选项是 limit、expires
- **AND** 没有 sector、stop、emergency、deviation

#### Scenario: close 的字段

- **WHEN** 读 `/plan close` 的定义
- **THEN** 必填项是 symbol，可选项是 limit、expires
- **AND** 没有 sector、quantity、stop、emergency、deviation

### Requirement: 命令的注册

Discord 适配器启动时 SHALL 在配置的服务器里注册 `/plan`，注册完成以后才开始处理交互：

- 注册为服务器命令，立即生效；不注册全局命令
- 每次启动都按代码里的定义覆盖这个机器人在该服务器的命令，重启以后仍然只有一个 `/plan`
- 注册失败（比如机器人没有 `applications.commands` 授权、Discord 报错）时，启动 SHALL 失败，报通知不可用，错误信息写明是注册命令失败和 Discord 给的原因，不含 token

#### Scenario: 启动时注册

- **WHEN** 启动 Discord 适配器
- **THEN** 服务器 3003 里有 `/plan`，带 buy、add、reduce、close 四个子命令
- **AND** 全局命令里没有 `/plan`

#### Scenario: 注册被拒

- **GIVEN** 机器人没有 `applications.commands` 授权，Discord 拒绝注册
- **WHEN** 启动
- **THEN** 启动失败，错误信息写明注册命令失败和 Discord 给的原因，没有 token

#### Scenario: 重启以后再注册

- **GIVEN** 上一次启动已经注册了 `/plan`
- **WHEN** 再启动一次
- **THEN** 服务器 3003 里仍然只有一个 `/plan`

### Requirement: 只接受本人

只有配置的用户 SHALL 能录入方案。别人运行 `/plan` 时，适配器 SHALL 立即回复一条只有他自己看得到的提示：只有配置的用户可以录入方案；不解析、不查持仓、不提交，并写一条警告日志。

#### Scenario: 别人运行命令

- **WHEN** 别人（4004）运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 他立即看到一条只有自己看得到的提示，说只有 <@1001> 可以录入方案
- **AND** 没有调用 ID 生成端口和提交接口；写了一条警告日志，有 4004

### Requirement: 应答和处理的先后

本人运行 `/plan` 时，适配器 SHALL 按下面的顺序处理：

1. 在 3 秒内延迟应答：Discord 只对本人显示「正在思考」。应答之前只做内存里的判断（是不是本人）
2. 取一个方案 ID；然后在 Discord 的事件处理之外查持仓的板块（只有减仓和清仓）、解析成方案、提交
3. 把「正在思考」换成回复，见「回复」；回复只有本人看得到

查持仓和提交运行期间，别的命令、按钮、告警和确认请求 SHALL 照常处理。延迟应答失败时（比如 Discord 报错），SHALL 写一条错误日志，不解析、不提交：人看到 Discord 提示命令失败，可以再运行一次。

#### Scenario: 先应答再提交

- **GIVEN** 提交接口要运行很久才返回
- **WHEN** 本人运行 `/plan reduce symbol:AAPL quantity:60`
- **THEN** 适配器先延迟应答，再查持仓、提交
- **AND** 查持仓和提交不在 Discord 的事件处理所在的线程里运行

#### Scenario: 提交期间照常处理别的交互

- **GIVEN** 本人运行了一条 `/plan`，提交接口还没返回
- **WHEN** 本人按下一条确认消息的「确认」，别人运行 `/plan`，调用方推送一条告警
- **THEN** 三件事都照常处理完，不等提交接口返回

#### Scenario: 延迟应答失败

- **GIVEN** Discord 对延迟应答报错
- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 写一条错误日志，没有调用提交接口

### Requirement: 字段的解析

适配器 SHALL 按下表把填的文字解析成方案的字段，再按 risk-rules spec「交易方案的格式」构造方案：

| 字段 | 写法 | 结果 | 不符合时的原因 |
| --- | --- | --- | --- |
| `symbol` | 去掉首尾空白 | 转成大写 | 不能为空 |
| `sector` | 去掉首尾空白 | 原样 | 不能为空 |
| `limit`、`stop` | 去掉首尾空白以后只有数字，可以带一个小数点和小数部分，比如 `180.50`、`0.9999` | 照写的位数转成 Decimal | 不是数字（带 `$`、逗号、负号、指数，或者以小数点开头）；不大于 0 |
| `emergency`、`deviation` | 和价格一样的数字，后面可以带一个 `%` | 数字 ÷ 100，比如 `8` 和 `8%` 都是 0.08，`7.5` 是 0.075 | 不是数字；不大于 0 或不小于 100 |
| `expires` | `年-月-日`，比如 `2026-10-16` | 这个交易日的收盘时刻（UTC），提前收盘的日子用提前的时刻 | 写法不对或日期不存在；不是交易日；超出交易日历的范围 |

- 订单类型：买入和加仓是限价单；减仓和清仓填了 `limit` 是限价单，没填是市价单
- 方案 ID 由 ID 生成端口给出，见「方案 ID」；减仓和清仓的板块见「减仓和清仓的板块」
- 解析或构造方案不通过时，SHALL 不提交，回复格式错误，一次列出全部原因，每条写明是哪个字段和填的文字。构造方案时的原因（比如不符合最小变动单位）也照样列出

#### Scenario: 填全了的买入

- **WHEN** 本人运行 `/plan buy symbol:aapl sector:科技 quantity:100 limit:180.50 stop:171 emergency:8 deviation:5% expires:2026-10-16`
- **THEN** 提交接口收到方案：方案 ID U1，买入 AAPL，板块科技，100 股，限价单，限价 180.50，止损价 171，紧急阈值 0.08，允许价格偏离 0.05，有效期 2026-10-16 20:00Z

#### Scenario: 只填必填项

- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 提交接口收到的方案没有紧急阈值、允许价格偏离和有效期

#### Scenario: 加仓

- **WHEN** 本人运行 `/plan add symbol:MSFT sector:科技 quantity:200 limit:410 stop:395 deviation:5`
- **THEN** 提交接口收到方案：加仓 MSFT，200 股，限价单，限价 410，止损价 395，允许价格偏离 0.05

#### Scenario: 价格的写法

- **WHEN** 买入命令的限价分别填 `180.50`、` 180.5 `、`0.9999`
- **THEN** 方案的限价分别是 180.50、180.5、0.9999
- **WHEN** 限价填 `18o.5`、`$180`、`1,000`、`1e3`、`-5`、`.5`
- **THEN** 回复格式错误，原因是限价不是数字，带上填的文字；没有提交

#### Scenario: 价格不大于 0

- **WHEN** 买入命令的止损价填 `0` 或 `0.00`
- **THEN** 回复格式错误，原因是止损价必须大于 0；没有提交

#### Scenario: 百分数

- **WHEN** 买入命令的紧急阈值分别填 `8`、`8%`、`7.5`、`99.99`
- **THEN** 方案的紧急阈值分别是 0.08、0.08、0.075、0.9999（是否在范围内由风控检查）
- **WHEN** 紧急阈值填 `0.08`
- **THEN** 按百分数理解，方案的紧急阈值是 0.0008

#### Scenario: 百分数不对

- **WHEN** 买入命令的允许价格偏离填 `0`、`100`、`120%`、`8%%`、`abc`
- **THEN** 回复格式错误，原因写明允许价格偏离必须是大于 0、小于 100 的百分数；没有提交

#### Scenario: 有效期

- **WHEN** 有效期分别填 `2026-10-16`（周五）、`2026-11-02`（夏令时结束后的周一）、`2026-11-27`（感恩节次日，13:00 提前收盘）
- **THEN** 方案的有效期分别是 2026-10-16 20:00Z、2026-11-02 21:00Z、2026-11-27 18:00Z

#### Scenario: 有效期不是交易日

- **WHEN** 有效期填 `2026-10-17`（周六）或 `2026-11-26`（感恩节）
- **THEN** 回复格式错误，原因是这天不是交易日；没有提交

#### Scenario: 有效期写法不对

- **WHEN** 有效期填 `10/16`、`20261016`、`2026-02-30`
- **THEN** 回复格式错误，原因写明有效期要写成 `年-月-日` 的交易日；没有提交

#### Scenario: 有效期超出日历

- **WHEN** 有效期填一个超出交易日历覆盖范围的日期
- **THEN** 回复格式错误，原因是超出交易日历的范围；没有提交

#### Scenario: 股票代码和板块的空白

- **WHEN** 本人运行买入命令，symbol 填「 brk.b 」、sector 填「 金融 」（前后都有空格）
- **THEN** 方案的股票是 BRK.B，板块是「金融」
- **WHEN** symbol 或 sector 只有空格
- **THEN** 回复格式错误，原因是股票或板块不能为空；没有提交

#### Scenario: 多处错误一起列出

- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:abc stop:0 emergency:100`
- **THEN** 一条回复里列出三条原因：限价不是数字、止损价必须大于 0、紧急阈值必须小于 100
- **AND** 没有提交

#### Scenario: 不符合最小变动单位

- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.505 stop:171`
- **THEN** 回复格式错误，原因写明限价 180.505 不符合最小变动单位；没有提交

### Requirement: 减仓和清仓的板块

减仓和清仓 SHALL 用这只股票持仓的板块构造方案，人不用填：

| 查持仓的结果 | 处理 |
| --- | --- |
| 有持仓 | 用它的板块，接着解析和提交 |
| 没有持仓 | 回复：没有这只股票的持仓，不能减仓或清仓；不提交 |
| 查询出错 | 回复：查持仓出错和原因，没有提交，可以再录入一次；写错误日志 |

买入和加仓用填的板块，SHALL NOT 查持仓。

#### Scenario: 市价减仓

- **WHEN** 本人运行 `/plan reduce symbol:aapl quantity:60`
- **THEN** 提交接口收到方案：方案 ID U1，减仓 AAPL，板块科技，60 股，市价单，没有限价和有效期

#### Scenario: 限价清仓

- **WHEN** 本人运行 `/plan close symbol:AAPL limit:185 expires:2026-10-16`
- **THEN** 提交接口收到方案：清仓 AAPL，板块科技，不写股数，限价单，限价 185，有效期 2026-10-16 20:00Z

#### Scenario: 没有持仓

- **WHEN** 本人运行 `/plan close symbol:MSFT`
- **THEN** 回复说没有 MSFT 的持仓，不能清仓
- **AND** 没有调用提交接口

#### Scenario: 查持仓出错

- **GIVEN** 查持仓时抛错（比如数据库暂时连不上）
- **WHEN** 本人运行 `/plan reduce symbol:AAPL quantity:60`
- **THEN** 回复写明查持仓出错和原因、没有提交；写了一条错误日志
- **AND** 没有调用提交接口

#### Scenario: 买入不查持仓

- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 没有查持仓，方案的板块是填的「科技」

### Requirement: 方案 ID

每条要提交的命令 SHALL 从 ID 生成端口取一个新的方案 ID（UUID），一条命令只提交一次。内容完全相同的两条命令是两个方案，有两个不同的方案 ID。正式运行时 ID 生成端口给出随机的 UUID（版本 4），每次都不同。

#### Scenario: 同样的命令运行两次

- **WHEN** 本人连续两次运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 提交接口先后收到两个方案，方案 ID 分别是 U1 和 U2，其余字段相同

#### Scenario: 随机的方案 ID

- **WHEN** 从正式的 ID 生成端口连续取 1000 个 ID
- **THEN** 都是版本 4 的 UUID，没有重复

### Requirement: 提交接口

方案解析成功以后，适配器 SHALL 把方案交给提交接口，提交接口返回风控结果（见 risk-rules spec「风控的输入和判定」）：

- 提交接口在风控得出结果以后返回，不等人工确认；通过或调整的方案由提交接口一方请求人工确认，确认消息照常发到配置的频道
- 提交接口抛错，表示没有拿到风控结果：方案可能已经提交，也可能没有
- 等提交接口返回最多等「提交超时」。超过时 SHALL 按超时回复；提交接口之后才返回的，SHALL 写一条日志，有方案 ID 和风控结果（或错误），不再回复

| 配置 | 默认值 | 要求 |
| --- | --- | --- |
| 提交超时 | 60 秒 | 大于 0，小于 15 分钟（Discord 只在应答后 15 分钟内接受回复） |

提交超时是 Discord 适配器的配置，不从环境变量读。

#### Scenario: 提交接口抛错

- **GIVEN** 提交接口抛错「数据库暂时连不上」
- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 回复写明提交出错和这个原因，以及方案 ID U1 可能已经提交：频道里出现这个方案的确认消息就照常确认或拒绝，没有出现再重新录入
- **AND** 写了一条错误日志，有方案 ID U1

#### Scenario: 提交超时

- **GIVEN** 提交接口 70 秒以后才返回通过
- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 60 秒时回复超过 60 秒没有得到风控结果，以及方案 ID U1 可能已经提交（同上）
- **AND** 70 秒时写一条日志，有方案 ID U1 和风控结果通过；不再回复

#### Scenario: 改提交超时

- **GIVEN** 提交超时配置为 10 秒，提交接口 20 秒以后才返回
- **WHEN** 本人运行一条买入命令
- **THEN** 10 秒时回复超时

#### Scenario: 提交超时不合法

- **WHEN** 提交超时配置为 0 秒、-1 秒或 15 分钟
- **THEN** 构造配置时报错
- **WHEN** 提交超时配置为 14 分钟
- **THEN** 构造成功

### Requirement: 回复

本人的每条命令 SHALL 得到一条只有他看得到的回复：

| 情况 | 回复的内容 | 是否提交 |
| --- | --- | --- |
| 格式错误 | 「方案格式不对，没有提交」，每条原因一行 | 否 |
| 没有持仓、查持仓出错 | 见「减仓和清仓的板块」 | 否 |
| 风控拒绝 | 「风控拒绝」，动作、股票和股数（清仓不写股数），不会下单；每条不通过的规则一行，写名称和说明；方案 ID | 是 |
| 风控通过 | 「风控通过」，动作、股票和风控给出的最终股数；请到配置的频道里的确认消息上确认；方案 ID | 是 |
| 风控调整 | 「风控调整」，动作、股票、方案原来的股数和调整后的股数（清仓只写调整后的），原因见确认消息；请到配置的频道里的确认消息上确认；方案 ID | 是 |
| 提交出错、提交超时 | 见「提交接口」 | 已调用 |

频道用 Discord 的频道标记（`<#频道 ID>`），点一下就能跳过去。

#### Scenario: 风控通过

- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`，提交接口返回通过，100 股
- **THEN** 回复「风控通过：买入 AAPL 100 股」，请他到 <#2002> 的确认消息上确认，以及方案 ID U1

#### Scenario: 风控调整

- **GIVEN** 提交接口返回调整，最终 120 股
- **WHEN** 本人运行 `/plan add symbol:MSFT sector:科技 quantity:200 limit:410 stop:395`
- **THEN** 回复「风控调整：加仓 MSFT 200 股 → 120 股」，原因见确认消息，请他到 <#2002> 确认，以及方案 ID U1

#### Scenario: 风控拒绝

- **GIVEN** 提交接口返回拒绝，不通过的规则是「降级模式：系统处于降级模式，暂停开仓」和「单笔计划风险：AAPL 的计划风险将是 3,040.00，超过上限 2,000.00」
- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 回复「风控拒绝：买入 AAPL 100 股」，不会下单，两条规则各一行，以及方案 ID U1

#### Scenario: 清仓的股数取风控结果

- **WHEN** 本人运行 `/plan close symbol:AAPL`，提交接口返回通过，100 股
- **THEN** 回复「风控通过：清仓 AAPL 100 股」
- **WHEN** 提交接口返回拒绝，不通过的规则是「可卖数量：AAPL 的可卖数量是 0」
- **THEN** 回复「风控拒绝：清仓 AAPL」，写着这条规则

### Requirement: 回复发不出去

把「正在思考」换成回复时 Discord 报错或网络不通，适配器 SHALL 写一条错误日志，有方案 ID（有的话）和回复的内容。已经提交的方案不受影响：确认消息照常发到频道。

#### Scenario: 回复失败

- **GIVEN** Discord 对回复报错
- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`，提交接口返回通过
- **THEN** 写一条错误日志，有方案 ID U1 和「风控通过：买入 AAPL 100 股」
- **AND** 提交接口只被调用了一次

### Requirement: 和确认按钮互不干扰

`/plan` SHALL 由单独的处理接收。确认消息的按钮照 discord-notification spec 处理：按钮的处理不应答 `/plan`，`/plan` 的处理不接收按钮。

#### Scenario: 收到 /plan

- **WHEN** 本人运行 `/plan close symbol:AAPL`
- **THEN** 只由 `/plan` 的处理应答一次；确认按钮的处理没有收到它

#### Scenario: 按下确认按钮

- **WHEN** 本人按下一条确认消息的「确认」
- **THEN** 按钮照 discord-notification spec 处理；`/plan` 的处理没有收到它

### Requirement: 日志

本人和别人的每条命令 SHALL 写日志：谁、哪个子命令、方案 ID（取了的话）、怎样处理的（不是本人、格式错误、没有持仓、查持仓出错、风控结果、提交出错、超时）。提交超时的命令在提交接口返回时再写一条，见「提交接口」。日志 MUST NOT 含 token。

#### Scenario: 格式错误的日志

- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:abc stop:171`
- **THEN** 写一条日志，有 1001、buy、方案 ID U1 和「格式错误」

#### Scenario: 风控结果的日志

- **WHEN** 本人运行 `/plan close symbol:AAPL`，提交接口返回通过
- **THEN** 写一条日志，有 1001、close、方案 ID U1 和风控结果通过、100 股
