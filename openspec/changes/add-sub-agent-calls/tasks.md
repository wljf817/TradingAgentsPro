# Tasks

## 1. 格式和上下文

- [x] 1.1 `contracts/sub_agent_context.py` 加 `SubAgentContext`，`contracts/agent_call.py` 加 `SubAgentReply`；先写 `sub-agent-calls` spec「子 agent 的上下文」的场景，再写 `gateway/domain/context.py` 的 `sub_agent_context`（design D5）；运行 `uv run pytest tests/contracts tests/modules/gateway` 通过

## 2. 网关

- [x] 2.1 先写 `signal-format` spec「子 agent 引用的条目」的场景，再写 `domain/signal.py` 的 `unknown_entries`（design D2）；运行 `uv run pytest tests/modules/gateway` 通过
- [x] 2.2 先改 `task-dispatch` spec「子 agent 的输出变成 Result」和 `agent-calls` spec「子 agent 的任务」的场景，再改 `received`、`TaskDispatchService.receive`、`run_task` 和 `ModelBackend.sub_agent`，去掉 `_parse`、`_location` 和 `Malformed`（design D1、D2）；改 `tests/fakes/model_backend.py`、替身和 `VendorRouter`；先写 `stand-ins` spec「子 agent 替身」的改动；运行 `uv run pytest tests/modules/gateway tests/integrations`、`uv run mypy` 通过
- [x] 2.3 `GatewayConfig` 加 `max_sub_agent_tool_calls`，默认 5（design D7）；运行 `uv run pytest tests/bootstrap` 通过

## 3. 子 agent 调用

- [x] 3.1 先写 `sub-agent-calls` spec「调用子 agent 的消息」「交回工具」「子 agent 的输出不合法」的测试（用 `tests/integrations/llm/server.py` 的测试服务器），再写 `integrations/llm/sub_agent.py` 和 `LlmBackend.sub_agent`，抽出 PM 和子 agent 共用的输出不合法处理（design D1、D4、D5）；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 3.2 先写 `sub-agent-calls` spec「工具返回过的内容」「工具次数」的测试，再接上记录函数（design D3）；运行 `uv run pytest tests/integrations/llm tests/modules/gateway` 通过
- [x] 3.3 写 `bootstrap/news_prompt.md`、`fundamentals_prompt.md`，只放占位；先写 `sub-agent-calls` spec「子 agent 的角色 prompt」的测试，再在 `bootstrap` 读入并注册（design D4）；运行 `uv run pytest tests/bootstrap` 通过

## 4. 日志

- [x] 4.1 先写 `program-log` spec「运行子 agent 的任务」的测试，再改 `case_entries.py` 和 `CaseFlow.run_task`（design D6）；运行 `uv run pytest tests/workflows` 通过

## 5. 文档

- [x] 5.1 完整版 Proposal：「Task / Result 通信」「共享上下文」写明子 agent 只看到任务内容、市场状态、这只股票的行情和相关事件，以及工具查到或代码算好的内容；「News」「Fundamentals」的工具次数改成每次运行；`grep 共享事实` 查不到子 agent 的相关句子

## 6. 验证

- [x] 6.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy` 和 `uv run lint-imports`，全部通过
- [ ] 6.2 在主机上用真实 key、占位 prompt 和一个假的读取工具跑一次 News，deepseek-flash 和 Haiku 5.5 各一次：调用工具以后用 `submit_signal` 交回，引用工具返回的 ID，经网关得到完成的 Result；记下是否被厂商接受和实际用量，写进 model-vendor-facts 的记忆
