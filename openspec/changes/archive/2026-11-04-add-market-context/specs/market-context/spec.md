# Spec Delta

## Purpose

给快照的市场状态提供背景事实：最近的 VIX 收盘值和接下来的重要日程，让模型判断大环境时不用靠训练记忆。

## ADDED Requirements

### Requirement: VIX 收盘值

行情模块 SHALL 按给定的交易日，向 VIX 源取全部收盘值，给出日期早于这个交易日的最近一个收盘值和它的日期。

- 没有早于这个交易日的收盘值时 SHALL 报 VIX 不可用
- VIX 源报不可用时 SHALL 原样报出，不重试

本要求的场景使用：VIX 源有 2026-10-12 收盘 18.5、2026-10-13 收盘 19.2、2026-10-14 收盘 21。

#### Scenario: 取最近的收盘值

- **WHEN** 取交易日 2026-10-14 的 VIX 收盘值
- **THEN** 结果是 2026-10-13 的 19.2

#### Scenario: 不取当天和之后的值

- **WHEN** 取交易日 2026-10-13 的 VIX 收盘值
- **THEN** 结果是 2026-10-12 的 18.5

#### Scenario: 休市隔开的日期

- **WHEN** 取交易日 2026-10-19 的 VIX 收盘值
- **THEN** 结果是 2026-10-14 的 21

#### Scenario: 没有更早的值

- **WHEN** 取交易日 2026-10-12 的 VIX 收盘值
- **THEN** 报 VIX 不可用

#### Scenario: VIX 源不可用

- **GIVEN** VIX 源报不可用
- **WHEN** 取交易日 2026-10-14 的 VIX 收盘值
- **THEN** 报 VIX 不可用，VIX 源只被请求一次

### Requirement: Cboe 数据源

适配器 SHALL 读 `https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`，第一行是表头 DATE、OPEN、HIGH、LOW、CLOSE，日期写成 MM/DD/YYYY，把每一行映射为日期和收盘值，收盘值用 Decimal。

下面的情况 SHALL 报 VIX 不可用，不重试：超时、连不上、回复不是 200、表头不对、某一行的日期或收盘值读不出来、没有数据行。

#### Scenario: 读文件

- **GIVEN** 文件有两行：10/13/2026，收盘 19.200000；10/14/2026，收盘 21.000000
- **WHEN** 读 VIX 收盘值
- **THEN** 结果是 2026-10-13 的 19.2 和 2026-10-14 的 21，按文件的顺序

#### Scenario: 超时

- **GIVEN** 请求超时
- **WHEN** 读 VIX 收盘值
- **THEN** 报 VIX 不可用

#### Scenario: 回复不是 200

- **GIVEN** 回复 403
- **WHEN** 读 VIX 收盘值
- **THEN** 报 VIX 不可用

#### Scenario: 格式不对

- **GIVEN** 表头缺少 CLOSE；或者某一行的收盘值读不出来；或者文件只有表头
- **WHEN** 读 VIX 收盘值
- **THEN** 都报 VIX 不可用

### Requirement: 重要日程

行情模块 SHALL 按给定的交易日，从配置的日程里给出日期不早于这个交易日、不晚于它之后第 N 个交易日的项，按日期排列，日期相同的按配置的顺序。N 是配置项，默认 10，MUST 大于 0。

日程的每一项有日期和类型，类型是 FOMC 决议、CPI、非农之一；FOMC 决议的日期是会议的第二天。

本要求的场景使用：日程配置了 2026-10-15 CPI、2026-10-28 FOMC 决议、2026-11-06 非农。

#### Scenario: 列出窗口里的项

- **WHEN** 取交易日 2026-10-14 的重要日程，N 是 10
- **THEN** 结果依次是 2026-10-15 CPI、2026-10-28 FOMC 决议，没有 2026-11-06 非农

#### Scenario: 当天的项

- **WHEN** 取交易日 2026-10-15 的重要日程
- **THEN** 结果里有 2026-10-15 CPI

#### Scenario: 窗口的边界

- **GIVEN** N 配置为 9
- **WHEN** 取交易日 2026-10-15 的重要日程
- **THEN** 结果里有 2026-10-28 FOMC 决议
- **WHEN** N 配置为 8
- **THEN** 结果里没有 2026-10-28 FOMC 决议

#### Scenario: 没有日程

- **WHEN** 取交易日 2026-11-09 的重要日程
- **THEN** 结果为空

#### Scenario: 配置不合法

- **WHEN** N 配置为 0
- **THEN** 配置报错
