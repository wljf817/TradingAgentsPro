# Tasks

## 1. 适配器给出消息

- [x] 1.1 按 model-adapter spec「这次尝试的消息」先写测试：工具往来、之前的对话、工具次数提示、输出不合法、服务端出错、超时、还原，两种接口都测，运行 `uv run pytest tests/integrations/llm` 确认失败
- [x] 1.2 `ModelReply`、`ModelCallError`、`InvalidOutput` 加 `transcript`，`LlmClient` 导出消息，`LlmBackend` 转交不合法时的消息，1.1 的测试通过

## 2. 调用记录的存储

- [x] 2.1 按 model-call-trace spec「调用记录的存储」先写测试（需要 OrbStack）：原样读回、按案件读、NUL、同时写入、表名和归属
- [x] 2.2 写 `ModelCallRecord`、`ModelCallStore`、`PostgresModelCallStore`、迁移 v0016 和 `tests/fakes` 的内存实现，2.1 的测试和 state-store 的迁移测试通过

## 3. 网关写记录

- [x] 3.1 按 model-call-trace spec「调用记录」「写入失败」先写测试：拿到输出、子 agent 的 task_id、重试和换模型、全部不可用、输出不合法、冷却跳过、到截止时间、替身、写入出错
- [x] 3.2 `AgentCallService._call` 写记录，`run_task` 传 `task_id`，`bootstrap/app.py` 装配，更新 `modules/gateway/__init__.py` 的说明，3.1 的测试和全部应用的测试通过

## 4. 收尾

- [x] 4.1 完整版 Proposal「程序日志」的 Agent 网关一行加一句：完整输入和输出单独存表，运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate add-model-call-trace --strict` 全部通过
- [ ] 4.2 提交并部署以后在主机冒烟：Sonnet 5.5、Opus 5.5、`deepseek-flash` 各跑一次带工具的运行和一次超时，确认消息能还原，含系统指令和工具返回，迁移 0016 已运行，不留下数据
