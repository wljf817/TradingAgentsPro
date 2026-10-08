# Proposal

## Why

M2 的完成标志是「手工录入的交易方案在模拟盘走完风控、确认、下单、止损和对账」。模型要到 M5 才接入，在那之前交易侧需要一个由人录入方案的入口。完整版 Proposal 里没有这个入口，它是 M2 用来驱动模拟盘的工具。add-discord-notify 已经接好了 Discord 机器人和确认按钮，人用同一个机器人录入方案最顺手。

add-alpaca-paper-broker 还留下一条：方案的限价或止损价不符合最小变动单位时，券商拒绝订单和止损单，止损单会永远挂不上。录入的方案是第一批真正下单的方案，要在下单之前挡住。

本变更的目的只有一个：**用 Discord 斜杠命令 `/plan` 录入交易方案：把人填的内容解析成 `TradePlan`，交给注入的提交接口（由「下单流程」实现），并把格式错误和风控结果用只有本人看得到的回复告诉他。** 方案格式里加上最小变动单位，是录入的方案能下单的前提。

引用的 Proposal（完整版）章节：

- 「风控引擎与执行」：结果只有通过、调整、拒绝；调整后的方案进入人工确认，确认消息里标明「已被风控调整」及原因；不满足规则的一律拒绝、退回（手工录入时退回给人）；通过后进入人工确认
- 「架构 · 风控与执行」：开仓类方案通过 Discord 按钮确认
- 「Agent」的 PM 交易方案：方向、数量（add-risk-rules 定为股数）、订单类型和限价、止损价、有效期和允许的价格偏离、紧急阈值。录入的字段照此，不含理由、论点和引用
- 「技术栈与部署」通知与人工确认一行：Discord 机器人（discord.py），只接受指定用户的操作；可靠性要求：Discord token 存环境变量

## What Changes

- 斜杠命令 `/plan`，四个子命令，每个只列这个动作允许的字段，必填项由 Discord 标出：
  - `buy`、`add`：`symbol`、`sector`、`quantity`、`limit`、`stop` 必填；`emergency`、`deviation`、`expires` 可选
  - `reduce`：`symbol`、`quantity` 必填；`limit`、`expires` 可选，不填 `limit` 就是市价单
  - `close`：`symbol` 必填；`limit`、`expires` 可选
  - 价格是文本，解析成 Decimal；紧急阈值和允许价格偏离按百分数填（`8` 或 `8%` 是 0.08）；有效期填一个交易日（`2026-10-16`），表示到这个交易日收盘
  - 减仓和清仓不填板块：取这只股票持仓的板块；没有持仓时直接回复，不提交
- 启动时用 `DISCORD_GUILD_ID` 注册为服务器内的命令（立即生效）；注册失败时启动失败
- 只接受 `DISCORD_USER_ID`：别人得到一条只有自己看得到的拒绝
- 每条命令一个新的方案 ID（UUID），由新增的 ID 生成端口给出；「下单流程」用它作工作流 ID
- 收到命令先在 3 秒内延迟应答，再在 Discord 的事件循环之外查持仓、解析、提交，最后补上回复，回复只有本人看得到：格式错误、没有持仓、风控拒绝（列出每条规则）、通过或调整（请到频道里的确认消息上确认）、提交出错或超时（默认 60 秒，可配置）
- 新增提交接口 `PlanIntake`（通知模块的 ports）：查持仓的板块；提交方案，返回风控结果。由「下单流程」实现、「启动与装配」注入
- 方案格式增加最小变动单位：限价和止损价在 1 美元及以上最多 2 位小数，1 美元以下最多 4 位小数，不符合时构造方案报格式错误。以后模型给出的方案也受这条约束
- 新增 ID 生成端口 `IdGenerator`（shared）和它的实现（随机 UUID）
- 连真实 Discord 的冒烟测试：注册命令，人在 Discord 里运行几条 `/plan`，检查回复

## Non-goals

- 下单工作流、DBOS、跑风控、预留、请求确认、等确认、下单：「下单流程」实现 `PlanIntake`
- 进程启动、把 `PlanIntake` 接到工作流：「启动与装配」；环境变量模板、机器人的 `applications.commands` 授权：「部署」
- 修改或撤回已提交的方案、列出方案、命令行录入
- 限制只能录入股票池里的股票：M2 没有股票池
- 方案的理由、论点、引用的 Result：手工方案没有
- 确认按钮、告警：add-discord-notify 已经做了，本变更不改它们的行为
- 程序日志：M4 才有；在那之前命令的处理写进 Python 的日志
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

- `manual-plans`：`/plan` 命令的注册、四个子命令的字段、字段的解析、方案 ID、减仓和清仓的板块、只接受本人、应答和回复、提交接口的结果和失败、日志

### Modified Capabilities

- `risk-rules`：「交易方案的格式」增加最小变动单位

## Impact

- **代码**：
  - 新增 `shared/ids.py`（`IdGenerator`）和 `integrations/random_ids.py`（`RandomIdGenerator`）
  - `contracts/trade_plan.py`：限价和止损价的最小变动单位
  - `modules/notification/ports.py`、`__init__.py`：新增并导出 `PlanIntake`
  - `integrations/discord/`：新增 `plans.py`（解析和回复的文字，不做 I/O）、`commands.py`（`PlanCommand`）；`config.py` 加提交超时；`gateway.py` 注册 `/plan` 并转交给 `PlanCommand`，`DiscordGateway` 多一个参数；`__init__.py` 导出 `PlanCommand`。`DiscordNotifier` 和按钮的处理不变
- **测试**：`tests/fakes/ids.py`、`tests/fakes/plan_intake.py`，`tests/modules/notification/test_ports.py` 检查替身满足 `PlanIntake`；`tests/contracts/test_trade_plan.py` 加最小变动单位；`tests/integrations/test_random_ids.py`；`tests/integrations/discord/` 加 `test_plans.py`、`test_commands.py` 和替身 `calls.py`，`test_config.py`、`test_gateway.py` 加用例；冒烟测试 `test_plan_smoke.py`，`test_bot_smoke.py` 跟着改 `DiscordGateway` 的构造
- **依赖**：无。斜杠命令用 discord.py 自带的 `app_commands`
- **配置**：`pyproject.toml` 的 Ruff 只放行 `integrations/random_ids.py` 调用 `uuid.uuid4`，和 `system_clock.py` 读系统时间一样
- **文档**：README 的冒烟测试命令写明要在 Discord 里运行 `/plan`
- **对后续变更的影响**：
  - 「下单流程」：
    - 实现 `PlanIntake.submit`：用方案 ID 作工作流 ID 启动下单工作流（同一个 ID 只启动一次，重复提交不会下两次单）；风控得出结果就返回，不等人工确认；在提交超时（默认 60 秒）之内返回
    - 手工录入的方案，四种动作通过或调整以后都请求人工确认（完成标志里有确认）；回复里的「请在确认消息上确认」以此为前提
    - `submit` 抛错表示没有拿到风控结果，适配器提示人「方案可能已经提交」
    - 实现 `PlanIntake.held_sector`：账户数据里这只持仓的板块（`HeldPosition.sector`），没有时为 None
  - 「启动与装配」：建 `RandomIdGenerator`、`PlanCommand(intake, ids, calendar, config)`，再建 `DiscordGateway(config, plans)` 交给 `DiscordNotifier`。integrations 不能 import workflows，`PlanIntake` 的实现由 bootstrap 把下单流程的入口包起来注入。配置 Python 日志
  - 「部署」：邀请机器人时 scope 选 `bot` 和 `applications.commands`；本人在频道里要有「使用应用命令」权限（默认就有）；命令的回复走交互，不需要别的频道权限
  - M3「人工确认的规则」：如果定下手工录入的减仓、清仓不用确认，本变更回复里的「请在确认消息上确认」要一起改
  - M4、M5：模型给出的方案也用 `IdGenerator` 生成方案 ID；价格不符合最小变动单位的方案按格式不合法处理

## 需要人决定

无。完整版没有手工录入方案，它是实现路线为 M2 定下的工具；对应的「风控引擎与执行」「技术栈与部署」两节没有和本变更矛盾或缺漏的地方。最小变动单位和其他格式规则一样只写在 spec 里，不改完整版。
