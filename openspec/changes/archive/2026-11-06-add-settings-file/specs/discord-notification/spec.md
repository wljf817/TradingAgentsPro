# Spec Delta

## MODIFIED Requirements

### Requirement: Discord 配置

Discord 适配器的配置 SHALL 如下，都写在配置文件的 `discord` 一节（settings-file spec），前 4 项是必填项：

| 配置 | 配置文件里的项 | 要求 |
| --- | --- | --- |
| 机器人 token | `discord.token` | 必填，非空 |
| 服务器 ID | `discord.guild_id` | 必填，正整数 |
| 频道 ID | `discord.channel_id` | 必填，正整数；告警和确认都发到这个频道 |
| 用户 ID | `discord.user_id` | 必填，正整数；只接受这个用户按按钮，告警和确认都 @ 他 |
| 连接超时 | `discord.connect_timeout` | 默认 30 秒，大于 0 |
| 发送超时 | `discord.send_timeout` | 默认 10 秒，大于 0 |

必填项没有写或者为空，都算缺少。缺少时 SHALL 报配置错误，列出缺少的全部项。

token MUST NOT 出现在代码、仓库里的文件、日志、错误信息、配置的字符串形式和测试输出里。

#### Scenario: 4 个必填项齐全

- **GIVEN** 配置文件的 `discord.token` 是某个 token，`discord.guild_id` 3003，`discord.channel_id` 2002，`discord.user_id` 1001
- **WHEN** 读配置文件
- **THEN** 服务器 3003、频道 2002、用户 1001，连接超时 30 秒，发送超时 10 秒

#### Scenario: 缺少必填项

- **GIVEN** 没有 `discord.guild_id`，`discord.user_id` 为空字符串，另外两项齐全
- **WHEN** 读配置文件
- **THEN** 报配置错误，错误信息里有 `discord.guild_id` 和 `discord.user_id`，没有 token

#### Scenario: ID 不是正整数

- **WHEN** `discord.channel_id` 是 "general" 或 0
- **THEN** 报配置错误，错误信息里有 `discord.channel_id`，没有 token

#### Scenario: token 不外露

- **GIVEN** token 是 "abc.def.ghi"
- **WHEN** 读出配置，把配置转成字符串，并在缺少或非法的项上报错
- **THEN** 字符串形式和错误信息里都没有 "abc.def.ghi"

#### Scenario: 超时不是正数

- **WHEN** 连接超时或发送超时配置为 0 秒
- **THEN** 构造配置时报错
