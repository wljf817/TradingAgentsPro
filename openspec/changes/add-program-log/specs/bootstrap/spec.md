# Spec Delta

## MODIFIED Requirements

### Requirement: 日志

程序 SHALL 把日志写到 stderr，每条一行开头依次是：UTC 时间（ISO 8601，到毫秒，以 `Z` 结尾）、级别、线程名、logger 名，然后是内容。异常带上异常栈。

- 级别 SHALL 是 INFO：DEBUG 不输出
- httpx 和 httpcore SHALL 只输出 WARNING 及以上：它们每个请求写一条 INFO，Alpaca 的每次查询都会写
- DBOS 的日志 SHALL 走同一个输出、同一种格式，每条只出现一次
- 日志 MUST NOT 含有环境变量的值，见「环境变量」
- 下面这些 SHALL 各写一条日志：程序启动（版本号和模式）、启动顺序里每一步的结果、启动完成、收到停止信号、每一项关闭、已停止。正常的结果用 INFO，有不一致和降级用 WARNING，失败用 ERROR
- 程序的动作（风控、订单和止损单、人工确认、对账、降级模式、紧急退出、告警，以及启动、停止和崩溃恢复）SHALL 写进程序日志（program-log spec），除了上一条要求的那些行，MUST NOT 再写进这里
- 这里另外写技术问题：异常和异常栈、券商和行情的超时或断线、取不到数据、Discord 的连接出错，以及 Discord 适配器收到的按下和命令（discord-notification spec、manual-plans spec）

#### Scenario: 时间用 UTC

- **GIVEN** 进程的本机时区是 UTC+14
- **WHEN** 在 2026-10-14T14:00:00.123Z 写一条 INFO 日志
- **THEN** 这一行以 `2026-10-14T14:00:00.123Z INFO` 开头，后面是线程名、logger 名和内容

#### Scenario: httpx 的请求日志不输出

- **WHEN** httpx 写一条 INFO 的请求日志，然后写一条 WARNING
- **THEN** 只有 WARNING 那条出现在 stderr

#### Scenario: DEBUG 不输出

- **WHEN** 程序自己的 logger 写一条 DEBUG 和一条 INFO
- **THEN** 只有 INFO 那条出现在 stderr

#### Scenario: DBOS 的日志

- **WHEN** 正常启动
- **THEN** DBOS 启动时的 INFO 日志出现在 stderr，格式和程序自己的日志相同，每条只出现一次

#### Scenario: 日志里没有密钥

- **GIVEN** `APCA_API_SECRET_KEY`、`DISCORD_BOT_TOKEN` 和数据库密码各是一个不会在别处出现的值
- **WHEN** 正常启动再停止；另外在 Alpaca 拒绝账户设置（401）时启动；另外在数据库连不上时启动
- **THEN** 三次运行的 stderr 里都没有这三个值
- **AND** 日志里的数据库地址，密码是 `***`

#### Scenario: 动作只写程序日志

- **GIVEN** 状态库里有一笔已提交的买单 P1（买入 BBB 100 股）；停机期间 Alpaca 那边 P1 已受理、累计成交 30
- **WHEN** 正常启动
- **THEN** 程序日志有 P1 的订单变化（已提交变为部分成交）
- **AND** stderr 里没有这次变化
