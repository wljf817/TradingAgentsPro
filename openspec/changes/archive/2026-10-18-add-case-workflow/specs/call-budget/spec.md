# Spec Delta

## ADDED Requirements

### Requirement: 一个案件的费用记录

Agent 网关 SHALL 按案件 ID 给出这个案件的全部费用记录（「费用的记录」），按记下的先后排列，不论记到哪个预算、记在哪个交易日。案件结束时调用方用它的条数作为案件记录的调用次数（case-records spec「案件记录的内容」）。读写出错时照「费用的存储」报错，MUST NOT 当作这个案件没有记录。

本 requirement 的场景使用本 spec 的基准数据。

#### Scenario: 一个案件的记录

- **GIVEN** K-1 的 PM 用 p-1 记下两条，K-3 的 Quant 用 s-1 记下一条，接着 K-1 的 Critic 用 c-1 记下一条
- **WHEN** 读 K-1 的费用记录
- **THEN** 依次是 PM p-1、PM p-1、Critic c-1 三条，没有 K-3 的

#### Scenario: 跨过收盘也都在

- **GIVEN** K-1 的 PM 在 2026-10-14 19:59Z 记下一条，在 2026-10-14 20:01Z（属于下一个交易日）再记下一条
- **WHEN** 读 K-1 的费用记录
- **THEN** 两条都在

#### Scenario: 没有记录的案件

- **WHEN** 读 K-9 的费用记录，K-9 没有记下过费用
- **THEN** 没有记录

#### Scenario: 读出错

- **GIVEN** 数据库连不上
- **WHEN** 读 K-1 的费用记录
- **THEN** 报错，不返回空
