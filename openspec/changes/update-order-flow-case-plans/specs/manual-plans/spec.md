# Spec Delta

## MODIFIED Requirements

### Requirement: 子命令和字段

`/plan` SHALL 有四个子命令，每个只有这个动作允许的字段。必填项由 Discord 标出，没填必填项时 Discord 不让发出命令；没有列出的字段在命令里填不了。

| 字段 | 含义 | `buy`（买入） | `add`（加仓） | `reduce`（减仓） | `close`（清仓） |
| --- | --- | --- | --- | --- | --- |
| `symbol` | 股票代码，文本 | 必填 | 必填 | 必填 | 必填 |
| `sector` | 板块，11 个选项之一（Discord 检查），见下 | 必填 | 必填 | 无，取持仓的板块 | 无，取持仓的板块 |
| `quantity` | 股数，整数，最小 1（Discord 检查） | 必填 | 必填 | 必填 | 无，卖出全部可卖数量 |
| `limit` | 限价，文本 | 必填 | 必填 | 可选：填了是限价单，不填是市价单 | 同减仓 |
| `stop` | 止损价，文本 | 必填 | 必填 | 无 | 无 |
| `emergency` | 紧急阈值，百分数，文本 | 可选 | 可选 | 无 | 无 |
| `deviation` | 允许价格偏离，百分数，文本 | 可选 | 可选 | 无 | 无 |
| `expires` | 有效期：到这个交易日收盘，文本 | 可选 | 可选 | 可选 | 可选 |

`sector` 的选项 SHALL 是 11 个 SPDR 板块 ETF，和股票池的板块相同（Proposal「股票池与寻股」）：选项名是中文板块名，选项的值是 ETF 的代码，方案的板块是选项的值：科技 XLK、通信服务 XLC、可选消费 XLY、必需消费 XLP、医疗 XLV、金融 XLF、能源 XLE、工业 XLI、材料 XLB、公用事业 XLU、房地产 XLRE。

买入和加仓 SHALL 是限价单。可选项不填时，方案里这一项不填，由风控用默认值（见 risk-rules spec 和 pre-order-recheck spec）。每个字段 SHALL 带一句中文说明，写明怎么填，比如「百分数，8 表示 8%」。

本 spec 的场景除非另有说明，都使用下面的基准数据：

- 配置的用户 ID 1001（「本人」），另一个用户 4004（「别人」）；频道 2002，属于服务器 3003
- ID 生成端口依次给出方案 ID U1、U2……（都是 UUID）
- AAPL 有持仓，板块「科技」；别的股票都没有持仓
- 提交接口没有另外说明时返回通过，最终股数是方案的股数（清仓是 100）
- 交易日历是纽交所日历；提交超时用默认值
- 命令写成 `/plan buy symbol:aapl quantity:100 …` 的形式，冒号后面是填的原样文字；`sector` 写的是选的选项名，比如 `sector:科技` 是选了「科技」，值是 XLK

#### Scenario: buy 的字段

- **WHEN** 读 `/plan buy` 的定义
- **THEN** 必填项依次是 symbol、sector、quantity、limit、stop，可选项是 emergency、deviation、expires
- **AND** quantity 是整数，最小值 1；sector 是 11 个选项；其余都是文本

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

#### Scenario: 板块的选项

- **WHEN** 读 `/plan buy` 和 `/plan add` 的 sector
- **THEN** 都有 11 个选项，名字依次是科技、通信服务、可选消费、必需消费、医疗、金融、能源、工业、材料、公用事业、房地产，值依次是 XLK、XLC、XLY、XLP、XLV、XLF、XLE、XLI、XLB、XLU、XLRE
- **AND** 不能填选项以外的文字

### Requirement: 字段的解析

适配器 SHALL 按下表把填的文字解析成方案的字段，再按 risk-rules spec「交易方案的格式」构造方案：

| 字段 | 写法 | 结果 | 不符合时的原因 |
| --- | --- | --- | --- |
| `symbol` | 去掉首尾空白 | 转成大写 | 不能为空 |
| `limit`、`stop` | 去掉首尾空白以后只有数字，可以带一个小数点和小数部分，比如 `180.50`、`0.9999` | 照写的位数转成 Decimal | 不是数字（带 `$`、逗号、负号、指数，或者以小数点开头）；不大于 0 |
| `emergency`、`deviation` | 和价格一样的数字，后面可以带一个 `%` | 数字 ÷ 100，比如 `8` 和 `8%` 都是 0.08，`7.5` 是 0.075 | 不是数字；不大于 0 或不小于 100 |
| `expires` | `年-月-日`，比如 `2026-10-16` | 这个交易日的收盘时刻（UTC），提前收盘的日子用提前的时刻 | 写法不对或日期不存在；不是交易日；超出交易日历的范围 |

- 订单类型：买入和加仓是限价单；减仓和清仓填了 `limit` 是限价单，没填是市价单
- 方案 ID 由 ID 生成端口给出，见「方案 ID」；买入和加仓的板块是选的选项的值（「子命令和字段」），不另外解析；减仓和清仓的板块见「减仓和清仓的板块」
- 解析或构造方案不通过时，SHALL 不提交，回复格式错误，一次列出全部原因，每条写明是哪个字段和填的文字。构造方案时的原因（比如不符合最小变动单位）也照样列出

#### Scenario: 填全了的买入

- **WHEN** 本人运行 `/plan buy symbol:aapl sector:科技 quantity:100 limit:180.50 stop:171 emergency:8 deviation:5% expires:2026-10-16`
- **THEN** 提交接口收到方案：方案 ID U1，买入 AAPL，板块 XLK，100 股，限价单，限价 180.50，止损价 171，紧急阈值 0.08，允许价格偏离 0.05，有效期 2026-10-16 20:00Z

#### Scenario: 只填必填项

- **WHEN** 本人运行 `/plan buy symbol:AAPL sector:科技 quantity:100 limit:180.50 stop:171`
- **THEN** 提交接口收到的方案没有紧急阈值、允许价格偏离和有效期

#### Scenario: 加仓

- **WHEN** 本人运行 `/plan add symbol:MSFT sector:科技 quantity:200 limit:410 stop:395 deviation:5`
- **THEN** 提交接口收到方案：加仓 MSFT，板块 XLK，200 股，限价单，限价 410，止损价 395，允许价格偏离 0.05

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

- **WHEN** 本人运行买入命令，symbol 填「 brk.b 」（前后都有空格），sector 选金融
- **THEN** 方案的股票是 BRK.B，板块是选项的值 XLF，不带空白
- **WHEN** symbol 只有空格
- **THEN** 回复格式错误，原因是股票不能为空；没有提交

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

买入和加仓用选的板块，SHALL NOT 查持仓。

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
- **THEN** 没有查持仓，方案的板块是选的 XLK
