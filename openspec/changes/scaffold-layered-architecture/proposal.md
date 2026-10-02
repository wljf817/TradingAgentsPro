# Proposal

## Why

接下来的路线是先在 `modules/<模块>/domain/` 里写纯逻辑（交易日历、风控、订单状态机等）。目前代码只有一个 hello-world，项目的架构约定（依赖单向、domain 不做 I/O、不读系统时间、模块只通过公开入口交互）还只能靠自觉遵守。

这些约定越晚落成检查越难补：第一个模块写进来之前把骨架和自动检查建好，后面每次提交都会在 CI 里被检查。

本变更的目的只有一个：**建立分层目录骨架，并把架构约定变成 CI 里的自动检查。**

引用的 Proposal 章节：

- 「架构」：只有决策层和审核层调用模型，其余全部是确定性代码。这是 domain 保持纯逻辑的来源
- 「调度器与运行模块」：第一版放在同一个 Python 进程里，按模块划分边界，以后需要时再拆开部署
- 「技术栈与部署」中的开发工具一行：uv、Ruff、mypy（strict）、pytest、GitHub Actions CI

## What Changes

- 新建 6 个层级包：`shared`、`contracts`、`modules`、`integrations`、`workflows`、`bootstrap`。包根和这 6 个包的 `__init__.py` 都只放 docstring，写明这一层放什么、能依赖谁
- **BREAKING**（只影响占位代码）：命令行入口 `tradingagentspro` 从包根移到 `bootstrap/cli.py`，输出从问候语改为版本号；包根 `__init__.py` 不再导出任何东西
- 定下模块内部的文件结构和 repo 的职责：
  - 存储接口放 `ports.py`
  - `repo.py` 放本模块的表，以及存储接口的数据库实现
  - `service` 只依赖接口，不直接用 repo
- 用 import-linter 声明三类检查：
  - 顶层分层：依赖只能向下；`workflows` 和 `integrations` 互相不能 import
  - 模块内部分层：`service` 和 `repo` 互相独立，往下依次是 `ports`、`domain`、`config`；模块里只允许出现这几种文件
  - 包和包之间不能循环依赖
- 写架构测试（基于 grimp 和 AST），覆盖 import-linter 表达不了的规则：
  - 模块之外只能 import 模块的包根（公开 API）
  - 纯区域（`shared`、`contracts`、各模块的 `domain` 和 `config.py`）只能 import 白名单里的包，也不能使用 `open`、`print` 等做 I/O 或动态执行代码的内置函数
  - 包根和层级包的 `__init__.py` 只有 docstring
  - 文件和文件之间也不能有循环 import
  - 禁止依赖本机时区的写法
- 每条检查规则，包括 import-linter 的契约，都有基于真实源码样例、持续运行的负例测试
- 收紧 Ruff 规则：
  - 禁用整个 `time` 模块；禁止直接读系统时间、直接生成随机 UUID
  - datetime 必须带时区
  - 禁止用浮点字面量构造 Decimal
  - 关掉中文全角标点的误报
- 测试基础：
  - `tests/` 目录结构镜像 `src/`
  - 测试进程固定在 UTC+14 时区运行，让偷偷依赖本机时区的代码在测试里暴露出来
  - 配置 Hypothesis 的 `dev` 和 `ci` 两套设置
- CI：增加 `lint-imports` 步骤；pytest 使用 Hypothesis 的 `ci` 设置
- 文档：
  - README 写明目录分层和常用命令
  - `openspec/config.yaml` 补上本次新定的约定，让之后的变更都能读到

## Non-goals

- 不写任何业务模块，不在 `modules/` 下建子包
- 不实现 Clock、交易日历和 Decimal 数值类型，这些属于下一个变更「交易日历与时钟」
- 不加入 DBOS、PydanticAI、Pydantic、SQLAlchemy、moomoo、discord.py 等运行依赖，到第一个用到它们的变更时再加
- 不设计共享状态库，不定配置文件格式和加载方式
- 不引入 pre-commit 和覆盖率门槛
- 不新增也不修改任何 spec

## Capabilities

### New Capabilities

无。本变更只涉及目录结构、开发工具和文档，没有系统行为变化，因此在 `.openspec.yaml` 中设置了 `skip_specs: true`。

### Modified Capabilities

无。

## Impact

- **代码**：
  - `src/tradingagentspro/` 新增 6 个层级包，入口移到 `bootstrap/cli.py`
  - `tests/` 重组：新增架构测试、时区守卫和 Hypothesis 配置，替换原来的冒烟测试
- **依赖**：新增开发依赖 import-linter、grimp、hypothesis；运行依赖不变
- **配置**：
  - `pyproject.toml`：入口脚本、Ruff 规则、import-linter 契约
  - `.gitignore`：Hypothesis 和 import-linter 的缓存目录
- **CI**：`.github/workflows/ci.yml` 增加一步，pytest 步骤增加环境变量
- **文档**：`README.md`、`openspec/config.yaml` 的 `context`
- **对后续变更的影响**：
  - 新模块必须按规定的文件结构建，存储代码按 repo 的职责划分来写
  - domain 只能使用白名单中的包，不能使用禁用的内置函数；要放宽，需要在变更里明确修改架构测试
  - 任何代码都不能依赖本机时区，时间一律从 Clock 获取
