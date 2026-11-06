# Tasks

## 1. 运行

- [x] 1.1 假服务器加多轮回答和工具调用，先写 `model-adapter` spec「运行」「读取工具」的测试，再加 `Tool`、`LlmClient.run`；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 1.2 先写「交回工具」的测试，再用 D3 的做法接上，只回文字抛 `InvalidOutput`；两种接口的请求里 `tool_choice` 都是 auto；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 1.3 先写「工具次数上限」的测试，再加 D4 的计数和请求数上限；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 1.4 先写 D5 的三种不合法和工具抛异常的测试，再把重试设成 0；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 1.5 先写「之前的对话」的测试，再接上 `history`；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 1.6 先写「用量」「超时」「调用错误」里运行的场景的测试，再加 D7 的求和；运行 `uv run pytest tests/integrations/llm` 通过

## 2. 网关

- [x] 2.1 测试里用到默认时限的地方改成 spec 的基准配置，再改 `GatewayConfig` 的三个默认值和 `ModelBackend` 的说明；运行 `uv run pytest tests/modules/gateway tests/workflows` 通过
- [x] 2.2 `integrations/llm/client.py` 的模块说明写明询问只发一个请求、运行可以发多个；`grep` 能查到

## 3. Proposal

- [x] 3.1 完整版 Proposal「Task / Result 通信」的任务时限改成 10 分钟，「News」「Fundamentals」的上限规则改成工具用完后只回提示，模型据此交出结果；`grep` 能查到

## 4. 验证

- [x] 4.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy` 和 `uv run lint-imports`，全部通过
- [x] 4.2 在主机上用真实接口冒烟：Sonnet、Opus 和 DeepSeek 各跑一次带读取工具和交回工具的运行，再跑一次次数用完的运行，用量和结果都对；结果写进 `model-vendor-facts` 记忆
