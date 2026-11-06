# Tasks

## 1. 输出不合法的格式和案件状态

- [x] 1.1 `contracts/agent_call.py` 加 `InvalidReply`、`PmResponseReply`，`PmReply` 和 `PmTurn.reply` 加上 `InvalidReply`；先写 `agent-calls` spec「对话的每一轮」的新场景（`tests/contracts`）；运行 `uv run pytest tests/contracts` 通过
- [x] 1.2 先写 `case-stages` spec「唤醒 PM 和结论」「PM 回应」的新场景，再写 `reject_response`；运行 `uv run pytest tests/modules/scheduler` 通过

## 2. 适配器

- [x] 2.1 先写 `model-adapter` spec「之前的对话」的新场景，再让 `LlmClient.run` 的问题可以为 `None`；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 2.2 先写 `model-adapter` spec「输出」的新场景，再给 `InvalidOutput` 加原因（design D6）；`ModelReply`、`Answered` 加 `served_model`，取回答里的模型名；运行 `uv run pytest tests/integrations/llm tests/modules/gateway` 通过

- [x] 2.3 先写 `model-adapter` spec「OpenAI 接口的工具调用带空的思考字段」「带之前的对话的运行第一个请求就失败」的测试，再改 `client.py`（design D10、D11）；运行 `uv run pytest tests/integrations/llm` 通过

## 3. PM 调用

- [x] 3.1 先写 `pm-calls` spec「调用 PM 的消息」「对话变成消息」的测试，再写 `integrations/llm` 里由 `PmRequest` 组消息的函数，复用网关导出的 `pm_context`；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 3.2 先写 `pm-calls` spec「交回工具」「PM 的输出不合法」的测试（用 `tests/integrations/llm/server.py` 的测试服务器），再写 `LlmBackend.pm`、`pm_respond`；改 `ModelBackend.pm_respond`、`VendorRouter`、`AgentCallService`、替身和 `CaseFlow` 的返回类型；运行 `uv run pytest tests/integrations tests/modules/gateway` 通过，`uv run mypy` 通过
- [x] 3.3 写 `bootstrap/pm_prompt.md`，只放占位；先写 `pm-calls` spec「PM 的角色 prompt」的测试，再写读取函数；`_case_side` 先组宪章和角色 prompt，交给 `LlmBackend`；运行 `uv run pytest tests/bootstrap` 通过

## 4. 案件工作流和日志

- [x] 4.1 先写 `case-workflow` spec「PM 的研究」「PM 回应」的新场景，再改 `_research`、`_respond`；运行 `uv run pytest tests/workflows` 通过
- [x] 4.2 先写 `program-log` spec「调度器的动作」的新场景，再改 `case_entries.py` 和 `CaseFlow._called`；运行 `uv run pytest tests/workflows` 通过
- [x] 4.3 先写 `agent-calls` spec「一次调用」和 `model-fallback` spec「什么算不可用」的新场景：PM 的输出不合法记费用、不切换模型；运行 `uv run pytest tests/modules/gateway` 通过

## 5. 文档

- [x] 5.1 完整版 Proposal：「PM」的工具表加 `respond_to_challenge`，「校验」写明 PM 的输出不合法退回重交，研究阶段用完是结论无效，回应阶段用完是系统故障；`grep respond_to_challenge` 能在「PM」一节查到

## 6. 验证

- [x] 6.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy` 和 `uv run lint-imports`，全部通过
- [x] 6.2 在主机上用真实 key 和占位 prompt 跑一次 PM 调用，Sonnet 5.5、Opus 5.5 和 deepseek-flash 各一次：研究带一轮对话，回应时只给 `respond_to_challenge`；记下是否被厂商接受、实际用量和回答里的模型名，写进 model-vendor-facts 的记忆
