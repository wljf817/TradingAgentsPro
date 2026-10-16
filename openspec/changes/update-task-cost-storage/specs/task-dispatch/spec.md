# Spec Delta

## ADDED Requirements

### Requirement: 任务的存储

Agent 网关 SHALL 把 Task 和 Result 存在状态库所在的数据库里（任务表 gateway_tasks、Result 表 gateway_results，state-store spec「表的归属和迁移」），只由 Agent 网关写入：

- 只追加：Task 生成时写入，Result 生成时写入；写入以后 MUST NOT 被修改或删除
- 读回的 Task 和 Result 和写入的完全相同：输入的顺序、信号的每一项、费用的每一位小数、耗时、原因、说明、模型和是不是备用模型；派发时刻、截止时间读回的是同一时刻
- 进程重启以后（用同一个数据库新建存储），SHALL 读到之前写入的全部 Task 和 Result：复用、轮次和任务数接着算，之前派发的任务照样能生成 Result
- 读写出错（数据库连不上、表不存在、存着的 Task 或 Result 读不出来）时 SHALL 报错：MUST NOT 当作没有任务或者任务还没有 Result，也 MUST NOT 当作已经写入
- 几个案件可以同时派发、同时生成 Result：每个案件的检查只看本案件的任务，互不影响
- 同一个案件的派发 SHALL 由调用方依次进行：一个案件一次只在一个阶段（case-stages spec「案件的阶段」），PM 和 Critic 不会同时派发。生成 Result 可以和派发同时进行

本 requirement 的场景里，子 agent 的主模型是 google/s-1，备用模型是 openai/s-2（model-fallback spec 的基准模型的配置）；"重启"指用同一个数据库新建存储和 Agent 网关，之后新生成的 Task 接着编号。

#### Scenario: 重启以后接着算

- **GIVEN** PM 派发价格请求、新闻请求；T-1 完成，T-2 还没有 Result
- **WHEN** 重启，PM 派发价格请求、财报请求
- **THEN** 价格请求复用 T-1；财报请求派发 T-3，PM 的第 2 轮；PM 用了 2 轮、3 个任务
- **WHEN** 接着 T-2 收到基准无信号
- **THEN** T-2 是无信号

#### Scenario: 原样读回

- **GIVEN** PM 派发财报请求（T-1）；2026-10-14 15:03:00Z T-1 收到 s-2 给出的基准信号，费用 0.0073
- **WHEN** 重启，取得 K-1 的任务
- **THEN** T-1 的输入依次是 E-3、E-4，派发时刻 2026-10-14 15:00Z，截止时间 2026-10-14 15:05Z，PM 的第 1 轮
- **AND** T-1 的 Result 是完成：输出是基准信号（置信度 0.6，数据截至 2026-10-14 14:30Z，引用的条目 E-1 和 E-2），耗时 3 分钟，费用 0.0073，模型 s-2、是备用模型

#### Scenario: 失败和被拒绝的原因读回不变

- **GIVEN** PM 派发 AAA 的价格请求（T-1，被拒绝），再派发价格请求（T-2）；2026-10-14 15:01:30Z T-2 收到 s-1 给出的评估窗口 4 的信号
- **WHEN** 重启，取得 K-1 的任务
- **THEN** T-1 的 Result 是被拒绝，原因是"股票不是案件的股票"，耗时和费用都是 0
- **AND** T-2 的 Result 是失败，原因是格式不合法，说明是"评估窗口不在配置的选项里"，模型 s-1、不是备用模型

#### Scenario: 读写出错

- **GIVEN** 数据库连不上
- **WHEN** PM 派发价格请求；或者取得 K-1 的任务
- **THEN** 都报错
- **GIVEN** 空数据库，没有升级
- **WHEN** PM 派发价格请求
- **THEN** 报错，不生成 Task
- **GIVEN** PM 派发价格请求，T-1 完成；存着的 T-1 的 Result 缺了状态
- **WHEN** 取得 K-1 的任务；或者 T-1 收到基准信号
- **THEN** 都报错，不当作 T-1 还没有 Result

#### Scenario: 几个案件同时派发

- **WHEN** PM 在 K-1 派发价格请求 1 到 6，同时 PM 在 K-3 派发 CCC 的价格请求 1 到 6
- **THEN** 两个案件各派发 6 个任务，都是各自的第 1 轮；K-1 和 K-3 的 PM 各用了 1 轮、6 个任务

## MODIFIED Requirements

### Requirement: 一个任务只有一个 Result

任务 SHALL 只有一个 Result，先写入的为准：已经有 Result 的任务再收到输出、调用失败或者记超时，SHALL NOT 改变 Result，给出已有的 Result。

两个调用同时为一个任务生成 Result（比如截止时间那一刻收到输出，同时记超时），都在对方写入以前读到这个任务还没有 Result 时，SHALL 只写入先写入的那个，两个调用都给出它。

#### Scenario: 超时以后才收到输出

- **GIVEN** PM 派发价格请求（T-1），2026-10-14 15:05:00Z 记超时
- **WHEN** 同样在 2026-10-14 15:05:00Z 收到基准信号
- **THEN** T-1 仍是超时，没有输出

#### Scenario: 完成以后再收到

- **GIVEN** PM 派发价格请求（T-1），2026-10-14 15:01:30Z 完成
- **WHEN** 2026-10-14 15:02:00Z 收到评估窗口 20 的信号；或者给出调用失败；或者 15:05:00Z 记超时
- **THEN** T-1 的 Result 都不变：完成，输出是基准信号

#### Scenario: 被拒绝的任务

- **GIVEN** PM 派发 AAA 的价格请求，T-1 被拒绝
- **WHEN** 收到基准信号
- **THEN** T-1 仍是被拒绝

#### Scenario: 同时生成 Result

- **GIVEN** PM 派发价格请求（T-1）
- **WHEN** 2026-10-14 15:05:00Z 收到基准信号，同时记超时；两个调用都在对方写入以前读到 T-1 还没有 Result，记超时的先写入
- **THEN** T-1 的 Result 是超时，没有输出；收到输出的那个调用给出的也是这个超时的 Result
- **AND** T-1 只有一个 Result
