# Tasks

## 1. ID 生成端口

- [x] 1.1 先写测试：
  - `tests/fakes/ids.py`：`FakeIds`（design D4、D11：依次给出 `UUID(int=1)`、`UUID(int=2)`……，记下给了几个）
  - `tests/integrations/test_random_ids.py`：spec「随机的方案 ID」（1000 个都是版本 4、没有重复）；类型检查 `ids: IdGenerator = RandomIdGenerator()`

  验证：确认因为 `tradingagentspro.shared.ids` 和 `integrations.random_ids` 不存在而失败
- [x] 1.2 实现 `src/tradingagentspro/shared/ids.py`（`IdGenerator`）和 `src/tradingagentspro/integrations/random_ids.py`（`RandomIdGenerator`）；`pyproject.toml` 的 Ruff `per-file-ignores` 只对 `integrations/random_ids.py` 放行 TID251（design D4）。验证：1.1 的测试通过；`uv run ruff check` 通过，并且临时在别的文件里调用 `uuid4` 仍然报 TID251；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 2. 方案格式的最小变动单位

- [x] 2.1 先写测试：`tests/contracts/test_trade_plan.py` 加 risk-rules delta「交易方案的格式」的四个新场景（1 美元及以上只能到分、低于 1 美元最多 4 位小数、正好 1 美元按 2 位小数算、退出方案的限价也检查），并检查错误信息里有「限价」或「止损价」和填的价格，限价和止损价都不对时两条都报。验证：新场景因为还没实现而失败，原有的测试仍然通过
- [x] 2.2 实现 `contracts/trade_plan.py` 的字段校验器（design D7），更新 `TradePlan` 的说明。验证：2.1 的测试通过；`uv run pytest` 全部通过（要开着 Docker），确认现有测试数据和 Hypothesis 策略没有不合规的价格；`uv run mypy` 通过

## 3. 提交接口

- [x] 3.1 在 `modules/notification/ports.py` 加 `PlanIntake`（design D5），包根导出、模块说明补一句；`tests/fakes/plan_intake.py` 写 `FakePlanIntake`（design D11：按映射回答 `held_sector`，`submit` 记下方案和运行的线程，默认返回通过，可以换结果、抛错、等 `threading.Event`，查持仓可以抛错），类型检查 `intake: PlanIntake = FakePlanIntake(...)`。验证：`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 4. 解析和回复的文字

- [x] 4.1 先写测试：
  - `tests/integrations/discord/test_config.py` 加「提交超时不合法」和默认 60 秒
  - `tests/integrations/discord/test_plans.py`：「字段的解析」的全部场景（用 `build_plan` 和 `tests/fakes/calendar.py` 的样例日历，方案 ID 用 `UUID(int=1)`）；「减仓和清仓的板块」里市价减仓、限价清仓用传入的板块；「回复」的全部场景、格式错误、没有持仓、查持仓出错、提交出错和超时的文字（design D10）
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `submit_timeout` 和 `integrations.discord.plans` 不存在而失败
- [x] 4.2 实现 `DiscordConfig.submit_timeout`（默认 60 秒，大于 0、小于 15 分钟）和 `src/tradingagentspro/integrations/discord/plans.py`（`PlanEntry`、`PlanFormatError`、`build_plan`、回复的文字，design D2、D3、D8、D10）。验证：4.1 的测试通过；测试进程在 UTC+14 时区下有效期仍然正确；`uv run mypy`、`uv run ruff check` 通过

## 5. PlanCommand

- [x] 5.1 先写测试：
  - `tests/integrations/discord/calls.py`：`FakeCall`（design D11：按顺序记下 `refuse`、`defer`、`reply`，记录可以和 `FakePlanIntake` 共用；可以安排 `defer` 或 `reply` 抛错）
  - `tests/integrations/discord/test_commands.py`：「只接受本人」「应答和处理的先后」「减仓和清仓的板块」（没有持仓、查持仓出错、买入不查持仓）「方案 ID」（同样的命令运行两次）「提交接口」（抛错、超时、改提交超时，超时配成 0.2 秒）「回复」（通过、调整、拒绝、清仓的股数，经过整个流程）「回复发不出去」「日志」（`caplog`）的场景；提交在工作线程里运行；提交停住期间别人的命令和同一个事件循环上的另一个协程照常完成
  - 每个测试的说明写明对应的 spec 场景

  验证：确认因为 `integrations.discord.commands` 不存在而失败
- [x] 5.2 实现 `src/tradingagentspro/integrations/discord/commands.py`（`CommandCall`、`PlanCommand`，design D8、D9）；包根导出 `PlanCommand`。验证：5.1 的测试通过，测试结束后没有留下运行中的工作线程；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 注册和转交 /plan

- [x] 6.1 先写测试 `tests/integrations/discord/test_gateway.py`：「子命令和字段」的四个场景（读 `PlanGroup` 的子命令和参数：名字、必填、类型、`quantity` 的最小值 1、每个字段都有中文说明）；注册的错误转换（`Forbidden`、`HTTPException` 转成 `NotificationUnavailableError`，信息里有「注册斜杠命令失败」和 Discord 给的原因）；`test_satisfies_the_protocol` 改成 `DiscordGateway(config, plans)`。验证：新用例因为还没实现而失败
- [x] 6.2 实现 `gateway.py`：`PlanGroup`、`InteractionCall`、`_Client` 带 `CommandTree`、`run` 在登录以后注册、注册错误的转换，`DiscordGateway(config, plans)`（design D8）；`on_interaction` 不变。`tests/integrations/discord/test_bot_smoke.py` 跟着传一个用替身提交接口的 `PlanCommand`。验证：6.1 和 add-discord-notify 的全部测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 冒烟测试

- [x] 7.1 写 `tests/integrations/discord/test_plan_smoke.py`（design D11），加 `discord_bot` 标记，4 个环境变量缺一个就跳过：注册和重启后再注册（REST 读服务器命令和全局命令）、人工运行四条 `/plan`（5 分钟内没运行完就失败）、核对替身收到的方案、日志里没有回复失败和 token。等待用 `threading.Event().wait`（`time` 被禁用）。README「常用命令」的 `discord_bot` 一行补上：运行中还要按提示在 Discord 里运行几条 `/plan`，只跑这一组时指定文件。验证：没有环境变量时 `uv run pytest -m discord_bot` 全部跳过，`uv run pytest` 不收集这些测试

## 8. 收尾

- [x] 8.1 对照两个 spec，确认每个场景都有对应的测试，测试名或说明里写明场景；只能在真实 Discord 上验证的（注册、全局命令里没有、只有本人看得到、真实的 3 秒应答）写明由冒烟测试覆盖。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 8.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
- [x] 8.3 由人在自己的终端运行冒烟测试（最后一项，需要人操作）：
  - 先准备：token 由人自己复制，只放进终端的环境变量；部署机器上如果已经用同一个 token 运行着程序，先停掉
  - 在终端里设好 `DISCORD_BOT_TOKEN`、`DISCORD_GUILD_ID`、`DISCORD_CHANNEL_ID`、`DISCORD_USER_ID`（不要写进仓库里的任何文件），直接运行 `uv run pytest -m discord_bot tests/integrations/discord/test_plan_smoke.py`；看到告警后按提示在 5 分钟内依次运行四条 `/plan`，确认每条回复都标着「只有你能看到」，文字和告警里说的一致
  - 只有启动报「注册斜杠命令失败」（没有授权）时，才在 Discord 开发者后台用 OAuth2 链接（scope 选 `bot` 和 `applications.commands`）重新邀请一次，不用踢出机器人，然后再运行一次。现在邀请时选 `bot` 通常已经包含斜杠命令的权限
  - 再运行一次 `uv run pytest -m discord_bot`，确认 add-discord-notify 的冒烟测试在 `DiscordGateway` 改了以后仍然通过

  验证：全部通过；和 spec 不符的地方先改 spec 和 design，再改代码和 4 到 6 组的测试
