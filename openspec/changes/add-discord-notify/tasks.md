# Tasks

## 1. Proposal 和依赖

- [x] 1.1 按人对 proposal「需要人决定」第 1 条的决定处理：同意推荐做法时，在完整版「技术栈与部署」通知与人工确认一行后补上"Discord 发不出去时只写程序日志，不另设备用通道，也不算健康异常；确认请求发不出去的方案按确认超时处理"；不同意前两点时，先改 spec「告警」「结束确认」和 design D3，再做后面的任务。验证：`grep -n "备用通道" "docs/AI Agent 美股交易系统需求 Proposal.md"` 的结果和人的决定一致；简版不改
- [x] 1.2 `uv add "discord.py>=2.7.1"`；`pyproject.toml` 的 pytest 配置登记标记 `discord_bot`（连真实 Discord 的冒烟测试），`addopts` 改为 `-m "not alpaca_paper and not moomoo_opend and not discord_bot"`（design D9）。验证：`uv sync --locked` 成功，`uv run pytest -q` 全部通过，`uv run pytest --markers` 列出 `discord_bot`，`uv run mypy` 通过

## 2. 通知模块

- [x] 2.1 先写测试：
  - `tests/fakes/notifier.py`：`FakeNotifier`（design D10：记录告警、确认请求和报告的结果，按顺序给出消息 ID，可以安排下一次调用抛 `NotificationUnavailableError`）
  - `tests/modules/notification/test_config.py`：「确认请求和截止时刻」的确认超时不是正数，以及默认 30 分钟
  - `tests/modules/notification/test_service.py`：「确认请求和截止时刻」的默认 30 分钟的截止时刻、改确认超时、被风控拒绝的方案；「告警」的告警发不出去；「结束确认」的报告发不出去；「同步调用和失败」的请求确认失败时用例抛错。日志用 `caplog` 检查
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.modules.notification` 不存在而失败
- [x] 2.2 实现 `src/tradingagentspro/modules/notification/`：`domain/__init__.py`、`domain/confirmation.py`（`ConfirmationOutcome`、`ConfirmationRequest`、`PendingConfirmation`，design D2）、`ports.py`（`Notifier`、`DecisionHandler`、`NotificationUnavailableError`）、`config.py`（`NotificationConfig`，design D3）、`service.py`（`NotificationService`）、`__init__.py`（模块说明并导出，design 的模块边界）。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. Discord 配置和消息内容

- [x] 3.1 先写测试：
  - `tests/integrations/discord/__init__.py`
  - `tests/integrations/discord/test_config.py`：「Discord 配置」的全部场景；token 用假值，检查配置的 `repr`、`str` 和每种错误信息里都没有它（design D4）
  - `tests/integrations/discord/test_messages.py`（design D5、D6）：「确认消息」的全部场景（P1 到 P4 的正文和两个按钮）；「告警」的推送告警（正文）、文字里有 @everyone（`only_user` 只允许配置的用户）、文字太长；按钮的标签、颜色和 custom_id；custom_id 的编码和解析，包括别的前缀、选择不对、方案 ID 和秒数不合法时解析不了；四种结果的嵌入的标题、颜色和时刻
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `tradingagentspro.integrations.discord` 不存在而失败
- [x] 3.2 实现 `src/tradingagentspro/integrations/discord/config.py`（`DiscordConfig`、`from_env`）、`messages.py`（design D5、D6）和 `__init__.py`（包说明，导出 `DiscordConfig`）。验证：3.1 的测试通过；测试进程在 UTC+14 时区下时间戳仍然正确；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. DiscordNotifier

- [x] 4.1 先写测试：
  - `tests/integrations/discord/gateway.py`：`FakeGateway` 和 `FakePress`（design D10）
  - `tests/integrations/discord/test_notifier.py` 的第一部分：「启动和关闭」的全部场景（登录失败、频道不对由替身 Gateway 的 `run` 抛错模拟，连不上由 `run` 一直不 ready 模拟，连接超时配成 0.2 秒）；「告警」的推送告警经过 Gateway 发出、带 `only_user`；「重复请求同一个方案」的全部场景；「结束确认」的报告超时、报告作废、消息已被删掉；「同步调用和失败」的从多个线程同时调用、超过发送超时（配成 0.2 秒）、Discord 报错、网络中断以后恢复、连接意外中止（含错误日志）
  - 类型检查：`notifier: Notifier = DiscordNotifier(...)`
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `DiscordNotifier` 不存在而失败
- [x] 4.2 实现 `integrations/discord/notifier.py`：`ButtonPress`、`Gateway`、`DiscordNotifier` 的启动、关闭、三个同步方法、线程和重复请求的处理（design D7）；按钮先只解析 custom_id、其余留空。验证：4.1 的测试通过，测试结束后没有留下运行中的 `discord` 线程；`uv run mypy` 通过
- [x] 4.3 先写测试，`test_notifier.py` 的第二部分：「按钮的处理」的全部场景，包括每次按下写日志（`caplog`）；「结束确认」的按钮的结果和调用方的结果不一致、报告时回调还在运行、报告以后再按。按 design D10 检查回调运行的线程不是事件循环所在的线程、`FakePress` 的应答在回调之前，并用 `threading.Event` 让回调停住，检查这期间告警和别人的按钮照常处理。验证：确认按钮的场景因为还没实现而失败，第一部分的测试仍然通过
- [x] 4.4 实现按钮的处理（spec「按钮的处理」的表，design D7）和报告结果与按钮的先后（调用方报告的结果最后显示）。验证：4.1 和 4.3 的测试全部通过；`uv run mypy`、`uv run ruff check` 通过

## 5. DiscordGateway

- [x] 5.1 先写测试 `tests/integrations/discord/test_gateway.py`：错误转换（design D8：改消息时 `discord.NotFound` 被忽略；`discord.HTTPException`、`aiohttp.ClientError`、`OSError` 转成 `NotificationUnavailableError`，信息里有状态码或原因）。验证：确认因为 `gateway.py` 不存在而失败
- [x] 5.2 实现 `integrations/discord/gateway.py`：`DiscordGateway`（`run`、`send`、`edit`、`close`，design D8）、`InteractionPress`、错误转换；包根导出 `DiscordGateway`、`DiscordNotifier`。类型检查 `gateway: Gateway = DiscordGateway(...)`。验证：5.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 6. 连真实 Discord 的冒烟测试

- [x] 6.1 写 `tests/integrations/discord/test_bot_smoke.py`（design D10），全部加 `discord_bot` 标记，4 个环境变量缺一个就跳过：无效 token、频道不对、自动部分、人工部分（3 分钟内没人按就失败），全程检查日志里没有 token。等待用 `threading.Event().wait`（`time` 被禁用）。README 的「常用命令」加一行运行方法，写明要先设好 4 个环境变量，运行中要按 Discord 消息上的按钮。验证：没有环境变量时 `uv run pytest -m discord_bot` 全部跳过，`uv run pytest` 不收集这些测试

## 7. 收尾

- [x] 7.1 对照 spec，确认每个场景都有对应的测试，测试名或说明里写明场景；只能在真实 Discord 上验证的（手机收到推送、真实的 3 秒应答、token 无效）写明由冒烟测试覆盖。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 7.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
- [ ] 7.3 由人在自己的终端运行冒烟测试（最后一项，需要人操作）：
  - 先准备：
    - 用服务器里现有的机器人。只有机器人还不在服务器里时，才需要在 Discord 开发者后台建应用和机器人，再用 OAuth2 链接（scope 选 `bot`）邀请进服务器
    - token 由人自己在开发者后台的 Bot 页面重置后复制，只放进终端的环境变量
    - 不需要打开任何特权 intent
    - 在要用的频道里给机器人「查看频道」「发送消息」「嵌入链接」权限，另外加「读取消息历史」：冒烟测试读回消息、核对内容要用，程序本身不需要
    - 打开 Discord 的开发者模式，复制服务器、频道和自己的 ID
  - 在终端里设好 `DISCORD_BOT_TOKEN`、`DISCORD_GUILD_ID`、`DISCORD_CHANNEL_ID`、`DISCORD_USER_ID`（不要写进仓库里的任何文件），运行 `uv run pytest -m discord_bot`；看到「请在 3 分钟内按……」的告警后，按提示在手机或电脑上按「确认」，再按「拒绝」
  - 部署机器上如果已经用同一个 token 运行着程序，先停掉

  验证：全部通过；手机收到了告警和确认消息的 @ 推送；和 spec 不符的地方先改 spec 和 design，再改代码和 3 到 5 组的测试
