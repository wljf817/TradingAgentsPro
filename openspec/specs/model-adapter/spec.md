# model-adapter Specification

## Purpose
规定怎样向真实模型问一次：两种接口、一次尝试的边界、输出、用量、超时和调用错误，按厂商分派到替身或真实模型，以及厂商的 key 怎么读。

## Requirements

### Requirement: 两种接口

适配器 SHALL 支持下表的两种接口。厂商用哪种接口、地址是什么写在模型文件里（model-config spec「文件的内容」）：

| 接口 | 请求 |
| --- | --- |
| `anthropic` | 向厂商的地址发 Anthropic Messages API 的请求 |
| `openai` | 向厂商的地址发 OpenAI Chat Completions API 的请求 |

- 支持这两种接口之一的厂商 SHALL 只改模型文件就能用，不改代码
- 一次询问给出：模型、超时、说明、问题和输出的类型
- 每次请求 SHALL 带输出 token 的上限，上限是配置项"输出上限"，默认 16,000，正整数

本 spec 的场景用下面的基准数据：

- 厂商 acme：接口 `anthropic`，地址是测试服务器，key 是 K-1
- 厂商 orbit：接口 `openai`，地址是测试服务器，key 是 K-2
- "问一次"指向 acme/m-1 或 orbit/m-2 问一次，超时 30 秒，说明 "I"，问题 "Q"，输出的类型是文本；厂商没有写明时两家各问一次

#### Scenario: Anthropic 接口

- **WHEN** 向 acme/m-1 问一次
- **THEN** 测试服务器收到一个 Messages 请求：模型 m-1，说明 I，问题 Q，输出上限 16,000，key 是 K-1

#### Scenario: OpenAI 接口

- **WHEN** 向 orbit/m-2 问一次
- **THEN** 测试服务器收到一个 Chat Completions 请求：模型 m-2，系统消息是 I，用户消息是 Q，输出上限 16,000，key 是 K-2

#### Scenario: 改输出上限

- **GIVEN** 输出上限配置为 2,000
- **WHEN** 问一次
- **THEN** 请求的输出上限是 2,000

#### Scenario: 输出上限不合法

- **WHEN** 输出上限配置为 0
- **THEN** 配置报错

### Requirement: 一次尝试

一次询问 SHALL 是 model-fallback spec「什么算不可用」的一次尝试：只发一个请求。SDK 的重试和输出校验失败后的重试 MUST 关闭，重试和切换模型由网关负责。

#### Scenario: 失败不重试

- **GIVEN** 测试服务器回答 429
- **WHEN** 问一次
- **THEN** 测试服务器只收到一个请求，调用错误是限流

### Requirement: 输出

询问的输出类型 SHALL 是文本或一个 Pydantic 模型。输出类型是 Pydantic 模型时，请求 SHALL 按接口带上输出的 JSON schema：

| 接口 | 怎样要求结构化输出 |
| --- | --- |
| `anthropic` | 请求里带输出的 JSON schema |
| `openai` | JSON schema 写进系统消息，请求要 JSON 对象 |

- 输出类型是文本时，SHALL 原样给出模型回答的文本
- 模型的回答不符合输出类型时 SHALL 给出"输出不合法"，带上回答的原文和这次的用量。输出不合法不是调用错误，由调用方处理

#### Scenario: 文本

- **GIVEN** 模型回答 `{"a": 1}`
- **WHEN** 问一次
- **THEN** 输出是文本 `{"a": 1}`，请求里没有 JSON schema

#### Scenario: Anthropic 接口的结构化输出

- **GIVEN** 模型回答符合 R 的 JSON
- **WHEN** 向 acme/m-1 问一次，输出类型是 Pydantic 模型 R
- **THEN** 请求里带 R 的 JSON schema，没有强制调用工具，输出是 R

#### Scenario: OpenAI 接口的结构化输出

- **GIVEN** 模型回答符合 R 的 JSON
- **WHEN** 向 orbit/m-2 问一次，输出类型是 Pydantic 模型 R
- **THEN** 系统消息里有 R 的 JSON schema，请求的 `response_format` 是 `json_object`，没有强制调用工具，输出是 R

#### Scenario: 输出不合法

- **GIVEN** 模型回答 `not json`，用量输入 11、输出 7
- **WHEN** 问一次，输出类型是 Pydantic 模型 R
- **THEN** 测试服务器只收到一个请求，给出输出不合法，原文是 `not json`，用量输入 11、输出 7

### Requirement: 用量

拿到模型的回答时 SHALL 给出这次的用量，单位是 token：

| 项 | 内容 |
| --- | --- |
| 输入 | 这次请求的全部输入，包括缓存命中和缓存写入 |
| 输出 | 这次回答的全部输出，包括推理 |

#### Scenario: Anthropic 接口的用量

- **GIVEN** 回答的用量是 input_tokens 40、cache_read_input_tokens 50、cache_creation_input_tokens 10、output_tokens 7
- **WHEN** 向 acme/m-1 问一次
- **THEN** 用量输入 100、输出 7

#### Scenario: OpenAI 接口的用量

- **GIVEN** 回答的用量是 prompt_tokens 100，其中缓存命中 60；completion_tokens 9，其中推理 3
- **WHEN** 向 orbit/m-2 问一次
- **THEN** 用量输入 100、输出 9

### Requirement: 超时

超过询问的超时仍没有输出时，SHALL 取消这个请求，调用错误是超时，没有用量。

#### Scenario: 超时

- **GIVEN** 测试服务器 5 秒以后才回答
- **WHEN** 问一次，超时 1 秒
- **THEN** 大约 1 秒以后调用错误是超时，没有用量

### Requirement: 调用错误

没有拿到模型的回答时 SHALL 按下表给出 model-fallback spec「什么算不可用」的调用错误，错误信息 MUST NOT 含有 key：

| 情况 | 调用错误 |
| --- | --- |
| 连不上，或者连接中断 | 连接失败 |
| 回答 429 | 限流 |
| 回答 402 | 额度或余额用完 |
| 回答 401 或 403 | 认证失败 |
| 回答 500 及以上 | 服务端出错 |
| 模型拒绝回答；其他 4xx | 其他调用错误 |

#### Scenario: 连接失败

- **GIVEN** 地址是一个没有服务的端口
- **WHEN** 问一次
- **THEN** 调用错误是连接失败

#### Scenario: 按状态码归类

- **WHEN** 测试服务器回答 429；或者 402；或者 401；或者 403；或者 500；或者 529；或者 400
- **THEN** 调用错误依次是限流、额度或余额用完、认证失败、认证失败、服务端出错、服务端出错、其他调用错误

#### Scenario: 模型拒绝回答

- **GIVEN** 测试服务器的回答的 stop_reason 是 refusal
- **WHEN** 向 acme/m-1 问一次
- **THEN** 调用错误是其他调用错误

### Requirement: 同时调用

多个线程 SHALL 可以同时询问，互不影响：全部询问走同一个后台事件循环，询问的线程等到结果为止。

#### Scenario: 多个线程同时询问

- **WHEN** 8 个线程各向 acme/m-1 问 25 次
- **THEN** 200 次都拿到输出

### Requirement: 按厂商分派

网关调用模型时 SHALL 按模型的厂商在模型文件里的接口分派：`standin` 交给替身（stand-ins spec），`anthropic` 和 `openai` 交给真实模型的后端。一次调用的结果和错误 SHALL 原样交回网关。

#### Scenario: 替身和真实模型并存

- **GIVEN** PM 的模型是 standin/pm，News 的模型是 acme/m-1
- **WHEN** 网关调用 PM，再调用 News
- **THEN** PM 由替身回答，News 交给真实模型的后端

#### Scenario: 错误原样交回

- **GIVEN** 真实模型的后端给出调用错误限流
- **WHEN** 网关调用 News
- **THEN** 网关收到调用错误限流，按 model-fallback spec 处理

### Requirement: 厂商的 key

厂商的 key SHALL 取自模型文件（model-config spec「文件的内容」），只发给这家厂商的地址。错误信息和日志里 MUST NOT 出现 key 的值。

#### Scenario: key 来自模型文件

- **GIVEN** 模型文件里 News 的模型是 acme/m-1，acme 的 key 是 K-1
- **WHEN** 向 acme/m-1 问一次
- **THEN** 请求带的 key 是 K-1

#### Scenario: key 不进日志

- **GIVEN** 测试服务器回答 401
- **WHEN** 向 acme/m-1 问一次
- **THEN** 调用错误是认证失败，错误信息和日志里没有 K-1

### Requirement: 关闭

程序停止时 SHALL 关闭后台事件循环和各厂商的连接，见 bootstrap spec「停止」。还没有结果的询问 SHALL 取消。

#### Scenario: 停止

- **WHEN** 程序停止
- **THEN** 后台事件循环的线程已经结束，日志写一条「已关闭：模型调用」
