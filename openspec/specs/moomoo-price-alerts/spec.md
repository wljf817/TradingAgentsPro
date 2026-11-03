# moomoo-price-alerts Specification

## Purpose
moomoo 到价提醒用 OpenD 的到价提醒接口实现到价提醒：怎样认出系统设的提醒，到价提醒怎样换成 OpenD 的提醒类型、值、频率和时段，调用失败怎样报告，推送怎样交给事件检测。

## Requirements

### Requirement: 系统设的提醒

适配器 SHALL 只把备注是 `TAPro` 的到价提醒当作系统设的：

- 读系统设的提醒：向 OpenD 读美股市场的全部到价提醒，只留下备注是 `TAPro` 的
- 设上的提醒备注都是 `TAPro`
- 备注不同的提醒不出现在读到的结果里，适配器 SHALL NOT 删除或修改它们

"OpenD 上有 R1、R2"指 OpenD 的美股到价提醒列表里有：R1，US.BBB，PRICE_DOWN，值 95.0，备注 TAPro，key 101；R2，US.BBB，PRICE_DOWN，值 80.0，备注空，key 102。

#### Scenario: 只认备注 TAPro

- **GIVEN** OpenD 上有 R1、R2
- **WHEN** 读系统设的提醒
- **THEN** 只有一个：BBB 价格跌到 95
- **WHEN** 同步成没有任何到价提醒
- **THEN** OpenD 只收到一次删除，key 101；R2 还在

### Requirement: 提醒的设置

适配器 SHALL 按下表设置到价提醒，代码是股票代码加 `US.` 前缀：

| 到价提醒 | OpenD 的提醒类型 | OpenD 的值 |
| --- | --- | --- |
| 价格涨到 v | PRICE_UP | v |
| 价格跌到 v | PRICE_DOWN | v |
| 当日涨幅达到 v | CHANGE_RATE_UP | v × 100（OpenD 的涨跌幅以百分比为单位） |
| 当日跌幅达到 v | CHANGE_RATE_DOWN | v × 100 |
| 当日成交量达到 v | VOLUME_UP | v（股） |

- 频率是每日一次（ONCE_A_DAY），时段只有常规交易时段（OPEN）：盘前、盘后、夜盘不提醒
- OpenD 只保留价格和百分比的 3 位小数，成交量按 10 股向下取整；适配器 SHALL 先按同样的规则取整再设置，读回时按同样的规则比较，所以设过的提醒下一轮仍算"已经设着"
- 删除用读到的 key
- 同步时先删后设

#### Scenario: 设一个价格提醒

- **WHEN** 同步成 BBB 价格跌到 95
- **THEN** OpenD 收到一次新增：代码 US.BBB，类型 PRICE_DOWN，值 95，频率 ONCE_A_DAY，时段只有 OPEN，备注 TAPro

#### Scenario: 涨跌幅换成百分比

- **WHEN** 同步成 BBB 当日涨幅达到 0.03、当日跌幅达到 0.05
- **THEN** OpenD 收到两次新增：CHANGE_RATE_UP 值 3；CHANGE_RATE_DOWN 值 5

#### Scenario: 取整以后比较

- **GIVEN** OpenD 上有系统设的 US.BBB，PRICE_DOWN，值 50.123；US.BBB，VOLUME_UP，值 2000000
- **WHEN** 同步成 BBB 价格跌到 50.1234、BBB 当日成交量达到 2,000,005
- **THEN** OpenD 没有收到新增和删除

#### Scenario: 换了值的提醒

- **GIVEN** OpenD 上有 R1
- **WHEN** 同步成 BBB 价格跌到 90
- **THEN** OpenD 先收到删除 key 101，再收到新增 US.BBB PRICE_DOWN 值 90

### Requirement: 调用失败

OpenD 的接口返回值不是成功时，适配器 SHALL：

- 读到价提醒失败（超时、断线、没登录、出错）：报到价提醒不可用，这次不删也不设
- 一个提醒的删除或新增失败：这个提醒算失败，带上 OpenD 给的错误信息；其余照做
- 不重试，下一次同步再来

OpenD 没开时，每次调用最多等连接配置的超时（moomoo-quotes spec「连接配置」）。

#### Scenario: 读不到提醒列表

- **GIVEN** OpenD 读到价提醒列表返回错误 Connect timeout
- **WHEN** 同步成 BBB 价格跌到 95
- **THEN** 报到价提醒不可用，信息里有 Connect timeout
- **AND** OpenD 没有收到新增和删除

#### Scenario: 一个新增失败

- **GIVEN** OpenD 新增 US.CCC PRICE_UP 时返回错误"超出上限"
- **WHEN** 同步成 BBB 价格跌到 95、CCC 价格涨到 120
- **THEN** 结果是设了一个、失败一个：CCC 价格涨到 120，原因"超出上限"
- **AND** BBB 价格跌到 95 设上了

### Requirement: 推送的回调

适配器 SHALL 在 OpenD 行情连接上登记到价提醒推送的处理：

- 收到任何一个到价提醒推送时调用登记的回调一次，不看推送的内容
- 推送在 SDK 的回调线程里处理：回调只做叫醒，不阻塞
- 和行情共用同一个 OpenD 行情连接（moomoo-quotes spec「连接配置」）；SDK 断线重连以后登记照样有效，不用重新登记
- OpenD 只在价格越过提醒的值时推送：设上时已经满足的提醒不推送，要等价格离开再越过（2026-11-02 在部署主机上实测）。所以设上时已经满足的条件由事件检测的下一轮判断出来（price-alerts spec「推送」）

#### Scenario: 收到推送

- **GIVEN** 登记了回调
- **WHEN** OpenD 推送 US.BBB 的 PRICE_DOWN 提醒触发；之后推送一条备注不是 TAPro 的提醒触发
- **THEN** 回调被调用两次
