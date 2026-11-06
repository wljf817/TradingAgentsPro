# Tasks

## 1. 依赖和模型文件

- [x] 1.1 运行 `uv add "pydantic-ai-slim[anthropic,openai]"`；`uv run python -c "import pydantic_ai"` 通过，`uv run lint-imports` 通过
- [x] 1.2 先写 `model-config` spec 新场景的测试，再让 `bootstrap/models.py` 读 `vendors`，`load_models` 改成返回模型配置、网关配置和厂商；`tests/bootstrap/model_files.py` 跟着改；运行 `uv run pytest tests/bootstrap/test_models.py` 通过
- [x] 1.3 先写「第一次启动时生成」「已有的文件不改动」「仓库里的模型文件」「文件不存在」「不进 git」的测试，再把 `bootstrap/models.toml` 改成 `bootstrap/models.template.toml`，只留替身和注释掉的示例，加生成文件的函数，`.gitignore` 加 `config/`；运行 `uv run pytest tests/bootstrap/test_models.py` 通过
- [x] 1.4 先写「厂商写错」「错误信息里没有 key」的测试，再把 `api_key_env` 改成 `api_key`，读文件的模型设 `hide_input_in_errors=True`；运行 `uv run pytest tests/bootstrap/test_models.py` 通过

## 2. 问一次

- [x] 2.1 写测试用的假服务器（Anthropic 和 OpenAI 两种回答），先写 `model-adapter` spec「两种接口」「一次尝试」「输出」「用量」的测试，再加 `integrations/llm` 的配置和 `LlmClient`，用 D3、D4、D5 的做法；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 2.2 先写「超时」「调用错误」的测试，再加 D6、D7、D8；确认 402 和 529 的归类符合 spec，不符合时先更新文档；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 2.3 先写「同时调用」「关闭」的测试，再加关闭；运行 `uv run pytest tests/integrations/llm` 通过，`no_threads_left_behind` 式的检查通过

## 3. 分派和装配

- [x] 3.1 先写「按厂商分派」的测试，再加 `VendorRouter` 和 `LlmBackend`；运行 `uv run pytest tests/integrations/llm` 通过
- [x] 3.2 在 `start` 的装配这一步先生成模型文件、再读，建 `LlmClient` 和 `VendorRouter`，登记关闭「模型调用」，放在 OpenD 之后、数据库之前；`BootstrapConfig.models_file` 默认 `config/models.toml`，全程序测试指向模板；运行 `uv run pytest tests/bootstrap` 通过，本机的 `config/` 没有被测试碰过
- [x] 3.3 先写「模型调用不写请求日志」的测试，再把模型 SDK 用的 HTTP 库的 logger 加进 `bootstrap/logs.py` 的 `QUIET`，关掉横幅；运行 `uv run pytest tests/bootstrap/test_logs.py` 通过

## 4. 部署文件和文档

- [x] 4.1 去掉 `TRADINGAGENTSPRO_MODELS_FILE` 和环境变量模板里的模型 key，`deploy/tradingagentspro.env.example`、`tests/deploy/test_env_templates.py`、`bootstrap/settings.py` 回到原样；运行 `uv run pytest tests/deploy tests/bootstrap/test_settings.py` 通过
- [x] 4.2 `deploy/README.zh.md` 和 `deploy/README.md` 写明模型文件在 `config/`、自动生成、怎样填地址和 key、不在备份里；`grep config/models.toml deploy/README*` 两份都能查到
- [x] 4.3 完整版 Proposal「技术栈与部署」的可靠性要求改成模型厂商的 API key 存在不进 git 的模型文件；`grep` 能查到

## 5. 验证

- [x] 5.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy` 和 `uv run lint-imports`，全部通过；`uv build --wheel` 打出的包里有 `models.template.toml`
- [x] 5.2 地址、模型和 key 写进模型文件以后，在主机上用真实接口对 Anthropic 接口和 OpenAI 接口各问一次文本、一次结构化输出，用量和输出都对；结果写进 `model-vendor-facts` 记忆
