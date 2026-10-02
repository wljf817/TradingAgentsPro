# clock Specification

## Purpose

为全系统提供唯一的当前时间来源：业务代码只通过注入的时钟读取"现在"，得到的一律是 UTC 时间。测试可以换成可控的时钟，回放时也能换成按记录推进的时钟。

## Requirements

### Requirement: 时钟返回 UTC 时间

时钟 SHALL 只提供一个操作：读取当前时间。返回值带时区，时区是 UTC。结果 MUST 不受进程所在时区影响。

调用方 MUST NOT 假设两次读取的结果递增，因为系统校时可能让时间回退。

#### Scenario: 系统时钟在 UTC+14 的进程里返回 UTC 时间

- **GIVEN** 进程时区是 Pacific/Kiritimati（UTC+14）
- **WHEN** 读取系统时钟的当前时间
- **THEN** 返回值带时区，时区是 UTC，UTC 偏移为 0
