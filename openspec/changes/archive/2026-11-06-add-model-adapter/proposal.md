# Proposal

## Why

模型文件已经能写模型，但程序还只会调用替身。接真模型以前要有一条发请求的通道：按厂商分派到替身或真模型，把结果、用量和错误换成网关要的形式。这样每个 agent 才能单独切换，后面的 PM、Critic、子 agent 都用这一条。

## What Changes

- 加依赖 `pydantic-ai-slim[anthropic,openai]`
- 支持两种接口：Anthropic 和 OpenAI 兼容，不按厂商写代码
- 模型文件改到 `config/models.toml`：不进 git，不存在时启动自动生成，内容是模板，全是替身。每个部署有自己的一份，换地址、模型或 key 只改这个文件，重启生效
- 模型文件加 `vendors`：每家厂商的接口、地址和 API key；替身也写在这里
- 新增 `integrations/llm`：向模型问一次，发一个请求，处理超时、调用错误、用量和不合法的输出
- 全部调用走一个后台事件循环，SDK 的重试和输出的重试都关掉，重试和切换模型由网关负责
- 按厂商分派的 `ModelBackend`：替身的厂商交给替身，其他交给真实模型的后端

## Capabilities

### New Capabilities

- `model-adapter`：两种接口、一次询问、输出、用量、超时、调用错误、同时调用、按厂商分派、厂商的 key 和关闭

### Modified Capabilities

- `model-config`：「模型文件」改到 `config/`、自动生成，「文件的内容」加 `vendors`，「文件出错」
- `bootstrap`：「日志」「模拟盘的装配」「停止」
- `deployment`：「密钥」加模型厂商的 key

## Impact

- 代码：新增 `integrations/llm`；`bootstrap/models.toml` 改成模板 `bootstrap/models.template.toml`；改 `bootstrap/models.py`、`bootstrap/app.py`、`bootstrap/logs.py`、`.gitignore`
- 数据：没有迁移
- 依赖：`pydantic-ai-slim` 带来 anthropic、openai、httpx2
- 部署：不加环境变量；第一次启动时生成模型文件，它不在备份里

## 引用的 Proposal 章节

- 完整版「Agent」：备用模型里什么算不可用
- 完整版「调度器与运行模块」：每个 agent 调用有超时和重试次数，由网关执行
- 完整版「技术栈与部署」：PydanticAI 做模型调用，设置 max_tokens 上限。可靠性要求那句改成模型厂商的 API key 存在不进 git 的模型文件

## Non-goals

- `pm`、`pm_respond`、`critic`、`sub_agent`、`reviewer` 的真实调用：各自的变更做，所以线上继续是替身
- prompt、对话转成消息：属于「PM 调用」等变更
- 选定每个 agent 的真模型：改模型文件就行
- thinking、effort、提示缓存：用厂商的默认值，以后有需要再加
- 记下实际的模型版本：要改 Result 和案件记录，放到接入各 agent 时做
- 第三家厂商：写进 `vendors` 就能用，等定下 Critic 的备用模型再加

## 需要人决定

无
