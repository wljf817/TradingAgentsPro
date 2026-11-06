## MODIFIED Requirements

### Requirement: 输出

询问的输出类型 SHALL 是文本或一个 Pydantic 模型。输出类型是 Pydantic 模型时，请求 SHALL 按接口带上输出的 JSON schema：

| 接口 | 怎样要求结构化输出 |
| --- | --- |
| `anthropic` | 请求里带输出的 JSON schema |
| `openai` | JSON schema 写进系统消息，请求要 JSON 对象 |

- 输出类型是文本时，SHALL 原样给出模型回答的文本
- 拿到输出时 SHALL 同时给出回答里写的模型名，回答里没有时不给
- 模型的回答不符合输出类型时 SHALL 给出"输出不合法"，带上回答的原文、原因和这次的用量；原因写明哪里不合法。输出不合法不是调用错误，由调用方处理

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

#### Scenario: 回答里的模型名

- **GIVEN** 测试服务器回答的模型名是 m-1-20261001
- **WHEN** 问一次；或者运行
- **THEN** 输出带模型名 m-1-20261001

#### Scenario: 输出不合法的原因

- **GIVEN** 模型回答的 JSON 缺少 R 的字段 a
- **WHEN** 问一次，输出类型是 Pydantic 模型 R；或者运行，交回工具的类型是 R
- **THEN** 给出输出不合法，原因写明字段 a

### Requirement: 用量

拿到模型的回答时 SHALL 给出这次的用量，单位是 token；运行发了多个请求时是各请求之和，失败的运行给出已经拿到回答的各请求之和，一个回答都没有时没有用量：

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

#### Scenario: 运行的用量

- **GIVEN** 运行的两个请求的用量分别是输入 10、输出 2 和输入 20、输出 3
- **WHEN** 运行结束
- **THEN** 用量输入 30、输出 5

#### Scenario: 中途失败的运行的用量

- **GIVEN** 运行的第一个请求用量是输入 10、输出 2，第二个请求回答 503
- **WHEN** 运行
- **THEN** 调用错误是服务端出错，用量输入 10、输出 2

#### Scenario: 第一个请求就失败的运行没有用量

- **GIVEN** 运行的第一个请求回答 503
- **WHEN** 运行
- **THEN** 调用错误是服务端出错，没有用量

#### Scenario: 带之前的对话的运行第一个请求就失败

- **GIVEN** 之前的对话是问题 Q0、调用 lookup、lookup 的结果 R0；这次运行的第一个请求回答 503
- **WHEN** 运行
- **THEN** 调用错误是服务端出错，没有用量

### Requirement: 运行

适配器 SHALL 支持一次运行：模型调用读取工具，代码执行后把结果交回模型，直到模型交出结果。运行给出：模型、超时、说明、问题（可以不带，见「之前的对话」）、读取工具、交回工具、工具次数上限和之前的对话。两种接口走同样的步骤，请求的工具选择都是由模型决定，MUST NOT 强制调用工具。

本 requirement 和后面几个的场景里，"运行"指向 acme/m-1 或 orbit/m-2 运行一次，超时 30 秒，说明 "I"，问题 "Q"，读取工具 lookup，工具次数上限 5，没有之前的对话；厂商没有写明时两家各运行一次。

#### Scenario: 调用工具以后交出结果

- **GIVEN** 测试服务器第一次回答调用 lookup，第二次回答文字 "done"，没有交回工具
- **WHEN** 运行
- **THEN** 代码执行 lookup 一次；第二个请求带着 lookup 的结果；结果是文字 "done"

#### Scenario: 请求里的工具选择

- **WHEN** 向 acme/m-1 运行；或者向 orbit/m-2 运行
- **THEN** 请求的工具选择都是 auto，读取工具和交回工具都在请求里

### Requirement: 之前的对话

运行 SHALL 可以带之前的对话：按先后排列的问题、工具调用和工具结果。之前的对话 MUST NOT 带思考内容，所以一段对话可以交给别家的模型接着运行。

之前的对话以问题或工具结果结束时，运行 SHALL 可以不带问题：请求只有之前的对话，模型接着回答。

`openai` 接口的请求里，之前的对话中调用工具的回答 SHALL 带空的 `reasoning_content`：DeepSeek 开思考时缺这个字段会拒绝请求。

#### Scenario: 带着之前的对话运行

- **GIVEN** 之前的对话是问题 Q0、调用 lookup、lookup 的结果 R0
- **WHEN** 运行，问题 Q
- **THEN** 请求里依次有 Q0、这次调用、R0、Q，没有思考内容

#### Scenario: 不带问题接着回答

- **GIVEN** 之前的对话是问题 Q0、调用 lookup、lookup 的结果 R0
- **WHEN** 运行，不带问题
- **THEN** 请求里依次有 Q0、这次调用、R0，后面没有别的消息

#### Scenario: 只有问题的对话

- **GIVEN** 之前的对话是问题 Q0
- **WHEN** 运行，不带问题
- **THEN** 请求里只有 Q0

#### Scenario: OpenAI 接口的工具调用带空的思考字段

- **GIVEN** 之前的对话是问题 Q0、调用 lookup、lookup 的结果 R0
- **WHEN** 向 orbit/m-2 运行，不带问题
- **THEN** 这次调用的回答带 `reasoning_content`，内容为空
- **WHEN** 向 acme/m-1 运行，不带问题
- **THEN** 请求里没有这个字段，也没有思考内容
