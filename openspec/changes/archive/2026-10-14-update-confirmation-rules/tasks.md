# Tasks

## 1. Proposal

- [x] 1.1 按人对 proposal「需要人决定」的决定修改完整版 Proposal：同意推荐做法的条目按各条的「完整版的改法」改（第 1、2、3、5 条；第 4 条不用改）；不同意的先改 spec 和 design 再改 Proposal。验证：`grep -n "可配置为需要\|不需要人工确认，执行后通知\|拒绝或超时以后当天不再紧急退出\|已经决定自动执行" "docs/AI Agent 美股交易系统需求 Proposal.md"` 的结果和人的决定一致；简版不改

## 2. 确认规则

- [x] 2.1 先写测试 `tests/modules/notification/domain/__init__.py` 和 `tests/modules/notification/domain/test_rules.py`：confirmation-rules spec 两个要求的全部场景（「默认配置下紧急退出不请求确认」在 4.1 测），开关从 `NotificationConfig` 读，每个测试的说明写明对应的场景。验证：确认因为 `case_plan_confirmation` 不存在而失败
- [x] 2.2 实现（design D1）：`modules/notification/config.py` 加 `auto_execute_model_exits`、`confirm_emergency_exits`（默认 False，注释写含义）；新增 `domain/rules.py`（`CaseConfirmation`、`case_plan_confirmation`）；`__init__.py` 导出并更新模块说明。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture tests/modules/notification` 通过

## 3. 紧急退出的确认消息

- [x] 3.1 先写测试（design D2、D8）：
  - `tests/fakes/notifier.py`：`FakeNotifier` 加 `request_emergency_confirmation`，记进 `emergency_requests`，和别的调用共用 `fail_next` 和消息标识的编号
  - `tests/modules/notification/test_service.py`：请求紧急退出的确认的截止时刻 = 请求的时刻 + 确认超时（超时从配置读）；通知不可用时抛 `NotificationUnavailableError`
  - `tests/integrations/discord/`：discord-notification spec「紧急退出的确认消息」的全部场景（正文、按钮交给回调、截止以后按下、报告作废、重复请求、发不出去）
  - 每个测试的说明写明对应的场景

  验证：确认因为 `EmergencyConfirmationRequest`、`request_emergency_confirmation` 不存在而失败
- [x] 3.2 实现（design D2）：`domain/confirmation.py` 的 `EmergencyConfirmationRequest`；`ports.py` 的 `Notifier.request_emergency_confirmation`；`service.py` 的 `request_emergency_confirmation`；`integrations/discord/messages.py` 的 `emergency_confirmation_content`，`confirmation_view` 接受两种确认请求；`integrations/discord/notifier.py` 两种请求共用去重和发送。验证：3.1 的测试通过，`tests/integrations/discord` 和 `tests/workflows` 原有的测试不改仍然通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 4. 紧急退出流程

- [x] 4.1 先写测试 `tests/workflows/test_emergency_confirmation.py`（design D8）：`World` 加打开「紧急退出要确认」的参数，测试用 `EmergencyDecisions.deliver` 模拟按钮；覆盖 emergency-exit-flow spec「紧急退出的人工确认」的全部场景，「告警」新加的两个场景（熔断告警写明清仓要确认、到收盘还在等确认），「重启以后」新加的四个场景（用一个新的 `EmergencyFlow` 和同一套状态库、券商替身表示重启），以及 confirmation-rules spec「默认配置下紧急退出不请求确认」；`EmergencyDecisions` 的 `expect`、`deliver`、`take`、`close`（不在等时 `deliver` 返回 False，`close` 时已经送到的结果照样返回）。每个测试的说明写明对应的场景。验证：确认因为 `EmergencyDecisions` 不存在或构造参数不对而失败
- [x] 4.2 实现（design D3 到 D7）：`workflows/emergency_flow.py` 的 `EmergencyDecisions`、`Gate`、`_Record` 的确认状态；`EmergencyFlow` 构造参数加 `NotificationConfig`、`EmergencyDecisions`；`judge` 对每只持仓判断本地备用止损并处理越过确认；`advance` 按确认状态请求、等待、超时；`report` 收盘时作废还在等的确认；新的告警文字和 `breaker_text` 的参数；模块说明写明确认的部分。验证：4.1 的测试通过；`tests/workflows` 原有的紧急退出和巡检测试只改构造参数，场景不改，全部通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 5. 装配

- [x] 5.1 先写测试 `tests/bootstrap/test_app.py`：bootstrap spec「按钮的结果按方案送给紧急退出流程或下单流程」（紧急退出流程在等的方案只送给它；别的方案送给下单流程，下单流程不在等时照旧抛错）。验证：确认失败
- [x] 5.2 实现（design D4、D7）：`bootstrap/app.py` 建一份 `NotificationConfig` 和一个 `EmergencyDecisions`，按钮的回调先交给 `EmergencyDecisions.deliver`、不收下时交给 `deliver_decision`；`EmergencyFlow` 拿到两者；模块说明写上分发。验证：5.1 的测试和 `tests/bootstrap` 原有的测试通过

## 6. 收尾

- [x] 6.1 对照本变更的四份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的要求里原有的场景仍由原来的测试覆盖。验证：对应表写在完成报告里
- [x] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
