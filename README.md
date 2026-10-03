# TradingAgentsPro

以代码调度器为核心的多 agent 美股交易系统：只有出现值得关注的事件或检查到期时才开案件，由 PM 带领子 agent 研究并给出结论，交易方案经 Critic 审核、代码风控和人工确认后执行。

- 设计文档：[docs/](docs/)，先读 [Proposal 简版](docs/AI%20Agent%20美股交易系统需求%20Proposal%20简版.md)
- 实现路线和里程碑：[docs/实现路线.md](docs/实现路线.md)
- 规格和变更：[openspec/](openspec/)

## 目录分层

```
src/tradingagentspro/
  bootstrap/      程序入口和装配：选 live / paper / replay，把实现注入各模块
  workflows/      DBOS 工作流：把多个模块的用例串成流程（和 integrations 互相独立）
  integrations/   外部系统的适配器：实现各模块定义的接口
  modules/        业务模块：每个模块对应架构图里的一个节点
  contracts/      跨模块传递的 Pydantic 模型
  shared/         最底层的基础类型和协议
```

上层可以依赖下层，反过来不行。包根和这 6 个层级包的 `__init__.py` 只放说明，不写代码。

每个模块内部的结构是固定的：

```
modules/<模块>/
  __init__.py     公开 API：模块之外的代码只能 import 这里
  service.py      用例：只依赖 ports 里的接口、domain 和 config
  repo.py         本模块的表，以及存储接口的数据库实现
  ports.py        本模块需要的全部外部接口（Protocol）
  domain/         纯逻辑：不做 I/O，不读时间，不依赖本机时区
  config.py       阈值和默认值
```

## 常用命令

```bash
uv sync                      # 安装依赖
uv run ruff check            # 代码检查
uv run ruff format --check   # 格式检查
uv run mypy                  # 类型检查（strict）
uv run lint-imports          # 依赖方向检查
uv run pytest                # 测试
```

克隆后启用提交前检查（和 CI 跑同一套，任何一项失败就拒绝提交）：

```bash
git config core.hooksPath .githooks
```

`lint-imports` 按 `pyproject.toml` 里的 import-linter 契约检查三件事：层与层之间只能向下依赖，模块内部按上面的结构分层，包和包之间没有循环依赖。

import-linter 表达不了的规则写成架构测试，放在 `tests/architecture/`，随 `pytest` 一起运行：模块之外只能 import 模块包根，纯逻辑区域只能用白名单里的包和内置函数，层级包的 `__init__.py` 只放说明，文件之间没有循环 import，不依赖本机时区。每条规则都有用真实源码样例写的正例和负例测试。
