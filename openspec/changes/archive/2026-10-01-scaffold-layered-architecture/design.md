# Design

## Context

现状：

- `src/tradingagentspro/__init__.py` 只有一个打印问候语的 `main`；`tests/test_smoke.py` 测它
- `pyproject.toml` 没有运行依赖，开发依赖是 mypy、pytest、ruff
- Ruff 只选了 `E F I UP B`，mypy 是 strict
- CI 依次跑 ruff check、ruff format --check、mypy、pytest 和 openspec validate
- 本地这四项检查目前全部通过

动机见 proposal.md 的 Why。

**模块边界**：本次不涉及任何业务模块，也没有在模块之间传递的 Pydantic 模型。只建立层级包和检查规则。

工具的能力和局限都已在临时目录里验证过：

- **import-linter 2.15**
  - 能力：
    - `layers` 契约支持 `|` 表示互相独立的同层包、括号表示可选层、`containers` 用通配符、`exhaustive` 检查文件清单
    - 另有 `acyclic_siblings` 契约
    - 配置写在 `pyproject.toml`；契约可以带 `id`
    - `importlinter.cli.lint_imports(config_filename=..., limit_to_contracts=...)` 能在测试进程里对指定的配置文件和契约运行，并返回退出码
  - 局限一：`layers` 只检查层级包之间的依赖，不检查包根 `__init__.py`。包根 import `integrations` 时三份契约全部通过，而此时 `import tradingagentspro.shared` 会执行 integrations 的副作用
  - 局限二：`acyclic_siblings` 不看父包和它的子模块之间的 import。`domain/__init__ -> b -> a -> domain` 这样的环能通过检查，实际 import 时却报 "partially initialized module"
- **grimp 3.17**
  - `include_external_packages=True` 时，标准库和未安装的第三方包都会以顶层名出现在 import 图里，并带行号
  - 模块级 import 图（每个 `__init__` 单独算一个节点）配合标准库 `graphlib`，能找出上面那个经过包根的环
- **Ruff 0.16.9**
  - 能力：
    - `TID251`（banned-api）能识别 `datetime.now(UTC)`、`dt.datetime.now()`、`uuid.uuid4()` 等各种写法
    - 也能禁用整个模块（比如 `time`），并用 `per-file-ignores` 为单个文件放行
    - `RUF032`、`DTZ001` 生效
    - `RUF001`–`RUF003` 会把中文全角标点报成易混淆字符；`allowed-confusables` 可以只放行这些标点，西里尔字母等仍会报
  - 局限：DTZ 规则看不到 `datetime.fromtimestamp(ts).astimezone()` 和 `x.replace(tzinfo=None).timestamp()`。前者是因为链式调用 `astimezone()` 后 DTZ006 就不报了。这两种写法都会读取机器的本地时区，同一个时间戳在 UTC 和 UTC+14 的机器上会算出不同的日期
- **内置函数**：`open`、`print`、`input` 等内置函数的调用不产生 import，所有基于 import 的检查都看不到

## Goals / Non-Goals

**Goals:**

- 规则写在配置或测试里，违规时 CI 报出具体模块和行号，而不是靠代码评审发现
- 空骨架上所有检查都能通过；新模块按规定结构加进来后，检查自动覆盖它，不需要为每个模块单独加配置
- 每条检查规则都有持续运行的负例测试：用临时目录里的真实源码样例，走和正式检查完全相同的流程。这样规则写错或日后被改弱时，测试会失败
- 任何计算结果都不受机器所在时区影响

**Non-Goals:**

- 不检查"金额必须用 Decimal"这类类型层面的约定。这交给 mypy strict 和代码评审，本次只用 `RUF032` 拦截最常见的错误
- 不检查测试代码的依赖和写法。测试可以直接 import 任何模块的内部，方便单测 domain
- 静态检查不追求覆盖所有动态写法（比如 `getattr`），漏掉的由 import 白名单和时区守卫兜底，见 Risks

## Decisions

### D1 顶层分层；包根和层级包的 `__init__.py` 只放 docstring

```
bootstrap                    装配：选 live/paper/replay，把实现注入 service
    |
workflows | integrations     互相独立：工作流只认识 modules 的接口；适配器只实现接口
    |
modules                      业务模块
    |
contracts                    跨模块传递的模型
    |
shared                       最底层的基础类型和协议
```

用 import-linter 的 `layers` 契约实现，`containers = ["tradingagentspro"]`。上层可以 import 下层，反过来不行。`workflows | integrations` 表示两者互相不能 import。

包根 `tradingagentspro/__init__.py` 和 6 个层级包的 `__init__.py` 都只能有 docstring，注释不限。原因是 import 任何模块时，Python 都会先依次执行它所有上级包的 `__init__.py`：

- 包根如果 import 了 integrations，那么 import `shared` 也会执行 integrations 的副作用
- `modules/__init__.py` 如果 import 了各个模块，那么用到一个模块就会把所有模块都加载进来

import-linter 不检查这一点（见 Context），所以由架构测试（D4）检查。

入口因此放在 `bootstrap/cli.py`，入口脚本改为 `tradingagentspro.bootstrap.cli:main`，输出 `tradingagentspro <版本号>`，版本号用 `importlib.metadata` 读取。

- 考虑过：删掉入口脚本，等 bootstrap 有实际内容再加
  - 没有采用：保留入口可以顺带验证打包和脚本安装在 CI 里正常
- 考虑过：只要求包根为空，层级包允许重新导出
  - 没有采用：层级包重新导出会导致"用到一个就加载一片"，而直接写完整路径（比如 `tradingagentspro.shared.clock`）并不麻烦

### D2 模块内部的结构、分层和 repo 的职责

```
modules/<模块>/
  __init__.py   公开 API：别的代码只能 import 这里
  service.py    用例：只依赖 ports 里的接口、domain 和 config，不直接用 repo
  repo.py       本模块的表定义，以及存储接口的数据库实现
  ports.py      本模块需要的全部外部接口（Protocol），包括自己的存储接口
  domain/       纯逻辑
  config.py     阈值和默认值
```

用一个通用的 `layers` 契约实现：

- `containers = ["tradingagentspro.modules.*"]`
- `layers = ["(service) | (repo)", "(ports)", "(domain)", "(config)"]`
- `exhaustive = true`

依赖关系：

- service 和 repo 互相不能 import
- 两者都可以用 ports、domain 和 config。repo 实现 ports 里的存储接口，可以显式继承那个 Protocol
- ports 的函数签名可以使用 domain 的类型

**repo 的职责**：`repo.py` 放本模块的表定义，以及 ports 里存储接口的数据库实现，也就是本模块全部的数据库访问代码。service 只认接口，由 bootstrap 决定注入哪个实现：

- 单元测试：`tests/fakes/` 里的内存实现
- live、paper、replay：同一套数据库实现，各自连接不同的库。具体怎么分库到接线阶段再定

repo 放在模块里而不是 integrations，是因为表属于模块。再加上 D5 的规则，别的模块就读不到这些表。

- 考虑过：repo 只放表定义，访问代码放 integrations
  - 没有采用：表和访问代码分在两处，而且任何适配器都能碰任何模块的表
- 考虑过：repo 只放存储接口
  - 没有采用：和 ports 重复，表定义也没有地方放

每一层都用括号标成可选，因为模块是一点点长出来的，内核阶段只有 `domain` 和 `config`。`exhaustive` 要求模块里只能出现这几种文件，防止 `utils.py`、`helpers.py` 变成杂物堆。需要新的文件种类时，必须在某个变更里明确修改这个契约。

- 考虑过：只约束 domain 不能 import service、repo、ports
  - 没有采用：这样拦不住层与层之间其他方向的反向依赖，也拦不住杂物文件

### D3 不允许循环依赖：包级和模块级各检查一次

- **包级**：import-linter 的 `acyclic_siblings` 契约，`ancestors = ["tradingagentspro"]`，深度用默认值，覆盖整棵树。模块之间成环，说明边界划错了，跨模块的流程应该放进 workflows 编排
- **模块级**：架构测试（D4）用 grimp 生成完整的模块级 import 图，每个 `__init__` 单独算一个节点，再用标准库 `graphlib` 找环。失败时打印整条环路，以及每一步的模块名和行号

两者缺一不可：

- 包级检查不看父包和子模块之间的 import（见 Context），会漏掉运行时真的会报错的环
- 模块级检查只看单个文件之间的 import。比如 a 包里的某个文件依赖 b 包，b 包里另一个文件又依赖 a 包，这种包之间的环它看不出来

TYPE_CHECKING 里的 import 也算依赖，和 D4 一致。

- 考虑过：只禁止子模块 import 自己的上级包
  - 没有采用：只能堵住一种成环的方式，模块级找环更通用

### D4 import-linter 管能声明的规则，其余写成 pytest 架构测试

import-linter 负责三条：顶层分层（D1）、模块内分层（D2）、包级无环（D3）。三份契约分别带 `id`：`top-layers`、`module-layers`、`acyclic`，测试可以逐个运行它们。

下面这些规则 import-linter 表达不了，写成 pytest 架构测试，放在 `tests/architecture/`：

| 规则 | 出处 | 实现方式 |
| --- | --- | --- |
| 模块之外只能 import 模块包根 | D5 | grimp import 图 |
| 纯区域只能 import 白名单里的包 | D6 | grimp import 图 |
| 纯区域禁止使用的内置函数 | D6 | AST |
| 包根和层级包的 `__init__.py` 只有 docstring | D1 | AST |
| 模块级没有循环 import | D3 | grimp import 图 + `graphlib` |
| 禁止依赖本机时区的写法 | D7 | AST |

前两条为什么不用 import-linter：

- "只能 import 包根"：需要表达"模块 X 的内部只允许 X 自己 import"，这种自己引用自己的规则用通配符写不出来。如果给每个模块各写一份 `protected` 契约，新模块忘了加的时候，检查会悄悄失效
- "白名单"：import-linter 的 `forbidden` 只能列黑名单。黑名单拦不住新引入的依赖，等于默认放行

实现方式：

- 每条规则写成函数：输入包名，以及它的 import 图或源码；输出违规列表，每条带模块名、行号和原因
- 构建 import 图时用 `include_external_packages=True`，不缓存。TYPE_CHECKING 里的 import 和函数内的延迟 import 都算依赖

测试方式：

- **正例和负例都用真实源码样例**：在临时目录里按"相对路径 -> 源码"生成小包，走和正式检查完全相同的流程，也就是 grimp 读源码建图、`ast` 解析文件。不用手工构造的 import 图，因为它暴露不了工具自身的行为，比如 Context 里 `acyclic_siblings` 的局限
- **import-linter 契约也有持续的负例测试**：测试读取 `pyproject.toml` 里真实的 `[tool.importlinter]` 配置，把包名换成样例包的名字，写到临时目录，再用 `importlinter.cli.lint_imports` 按 `id` 逐个运行，断言违规样例让对应的契约报错。这样契约日后被改弱，测试就会失败
- **真实包检查**：另有一个测试对真实的 `tradingagentspro` 包运行全部规则，要求违规列表为空

grimp 本来就是 import-linter 的依赖，但测试代码会直接 import 它，所以显式声明为开发依赖，不依靠传递依赖。

### D5 模块之间只能通过包根交互

规则：对每个模块 X（`tradingagentspro.modules` 下的每个直接子包），X 以外的任何代码只能 import `tradingagentspro.modules.X` 本身，不能 import `tradingagentspro.modules.X.<任何子模块>`。

- 这条规则同样适用于其他模块、workflows、integrations 和 bootstrap
- integrations 实现 X 的端口时，需要的端口协议由 X 的 `__init__.py` 重新导出。bootstrap 需要的 service、repo 实现和 config 也一样

### D6 纯区域：import 白名单和禁用的内置函数

纯区域是 `shared`、`contracts`、`modules.*.domain`（含子模块）和 `modules.*.config`。

**import 白名单**：纯区域**直接** import 的东西必须在下表范围内。

| 区域 | 项目内可以 import | 第三方 | 标准库 |
| --- | --- | --- | --- |
| `shared` | `shared` | 无 | 白名单 |
| `contracts` | `shared`、`contracts` | pydantic | 白名单 |
| `modules.X.config` | `shared`、`contracts`、`X.config` | pydantic | 白名单 |
| `modules.X.domain` | `shared`、`contracts`、`X.domain`、`X.config` | 无 | 白名单 |

标准库白名单：`__future__`、`abc`、`bisect`、`collections`、`dataclasses`、`datetime`、`decimal`、`enum`、`fractions`、`functools`、`heapq`、`itertools`、`math`、`numbers`、`operator`、`re`、`statistics`、`types`、`typing`、`uuid`。

故意不放进白名单的有 `os`、`sys`、`io`、`pathlib`、`time`、`random`、`secrets`、`logging`、`asyncio`、`threading`、`socket`、`json`、`zoneinfo`、`builtins`、`importlib` 等。理由是纯逻辑不做 I/O、不写日志、不产生随机数，"现在"作为参数传入。

`datetime` 和 `uuid` 在白名单里，是因为要用它们提供的类型。`now()`、`uuid4()` 这类调用由 D7 的 Ruff 规则在全项目禁止。

**禁用的内置函数**：内置函数不需要 import，白名单管不到。所以用 AST 检查纯区域里对下列名字的任何引用，不只是调用，`f = open` 也算：

| 名字 | 原因 |
| --- | --- |
| `open`、`input`、`print`、`breakpoint` | 做 I/O |
| `exec`、`eval`、`compile`、`__import__`、`__builtins__` | 动态执行代码或动态 import，能绕过 import 检查 |
| `exit`、`quit` | 退出进程 |
| `id`、`hash` | 结果每次运行都可能不同，回放时无法重现 |

domain 不直接用 pydantic：

- domain 的类型用 frozen dataclass，校验放在边界上（contracts、config）
- 这样 domain 不绑定框架，Hypothesis 也能直接用 `st.builds` 构造输入
- 以后确实需要扩充白名单或放行某个内置函数时，在变更里修改架构测试中的常量，并在那个变更的 design 里写明理由

白名单和禁用名单都写成架构测试里集中的常量，每一项都带注释说明理由。

### D7 时间、随机数和时区

目标：任何计算结果都不受"现在是什么时候"和"机器设在哪个时区"影响，这样回放才能重现。只要求 datetime 带时区是不够的，原因见 Context 里 DTZ 的局限。

**Ruff（全项目）**：

- `select` 增加三组规则：
  - `DTZ`：datetime 必须带时区
  - `TID`：包括 banned-api，以及禁止 `from ..` 这种跨父包的相对 import
  - `RUF`：包括 `RUF032`，禁止用浮点字面量构造 Decimal
- `allowed-confusables` 放行中文全角标点：`，；：？！‘’（）～％＋－＝`
  - 没有采用：整条忽略 `RUF001`–`RUF003`。那样西里尔字母这类真正易混淆的字符也会漏掉
- `flake8-tidy-imports.banned-api` 禁止下列用法，提示信息写明应该改用什么：
  - 整个 `time` 模块：一次覆盖 `time()`、`monotonic()`、`localtime()`、`mktime()`、`strftime()`、`sleep()` 等。时间和等待都要走 Clock
  - `datetime.datetime.now`、`datetime.datetime.utcnow`、`datetime.datetime.today`、`datetime.date.today`
  - `uuid.uuid1`、`uuid.uuid4`：ID 要能在回放时重现，所以走 ID 生成端口
- 放行方式：只用 `per-file-ignores` 为具体文件放行 `TID251`
  - 本次只有 `tests/conftest.py` 需要放行，因为它要调用 `time.tzset()`（见下文动态守卫）
  - 下一个变更创建系统时钟的实现文件时，再为那个文件放行

**AST 检查（`src/` 全部代码，见 D4）**：

- `astimezone()` 必须显式传入时区。不带参数时会转换到本机时区
- 禁止 `replace(tzinfo=None)`。它会生成不带时区的时间，之后调用 `timestamp()`、`astimezone(...)` 都会按本机时区解释

**动态守卫（测试）**：`tests/conftest.py` 在测试开始前把进程时区设为 `Pacific/Kiritimati`（UTC+14），并调用 `time.tzset()` 让它生效。

- 美股常规交易时段对应的 UTC 时间，换到 UTC+14 都已经是第二天。所以偷偷依赖本机时区的代码，在测试里几乎都会算错日期
- 静态检查覆盖不到的写法，由它兜底
- `tests/test_timezone_guard.py` 确认守卫确实生效：用一个固定的 2026 年时间戳，断言本地时区的偏移是 +14 小时

被否决的方案：

- 考虑过：只加静态检查
  - 没有采用：依赖本机时区的写法太多，静态检查列举不完。比如用 `datetime.combine` 生成不带时区的时间，再做转换
- 考虑过：只在 CI 里设置 `TZ` 环境变量
  - 没有采用：这样本地运行测试时，守卫不生效

### D8 测试目录和 Hypothesis

```
tests/
  __init__.py
  conftest.py              时区守卫；注册 Hypothesis 配置
  test_timezone_guard.py   确认时区守卫生效
  architecture/            D4 的规则、样例包工具、规则的正负例测试、对真实包的检查
  bootstrap/               入口测试（替换原来的 test_smoke.py）
  fakes/                   测试替身（下一个变更放 FakeClock）
```

- 以后测试目录镜像 `src/` 的结构，例如 `tests/modules/risk_engine/domain/`
- 每个测试目录都放 `__init__.py`，作为包导入。这样：
  - 镜像结构里同名的测试文件（比如多个 `test_rules.py`）不会冲突
  - `tests.fakes` 可以直接 import
  - mypy 能把它们识别成 `tests.*` 包
- 考虑过：用 pytest 的 `importlib` 导入模式，测试目录不放 `__init__.py`
  - 没有采用：那样共享测试替身不方便，mypy 也要额外配置

Hypothesis 在 `tests/conftest.py` 里注册两套配置，用环境变量 `HYPOTHESIS_PROFILE` 选择，默认 `dev`：

- `dev`：`deadline=None`，其余用默认值。本地每次随机探索，失败样例存进 `.hypothesis/`
- `ci`：`deadline=None`、`derandomize=True`、`print_blob=True`、`max_examples=200`。同一个提交在 CI 里结果固定、可以重现，失败时打印复现所需的数据

两套都设 `deadline=None`：纯逻辑不需要测耗时，而默认 200ms 的时限在慢机器上会造成偶发失败。

- 考虑过：CI 也随机跑
  - 没有采用：长期看能多发现问题，但 CI 失败后重跑可能又通过，难以排查。随机探索交给本地运行

### D9 CI

在 Mypy 之后、Pytest 之前增加 `uv run lint-imports`。Pytest 步骤加环境变量 `HYPOTHESIS_PROFILE: ci`。架构测试随 pytest 一起运行，不单独加步骤。

`.gitignore` 增加 `.hypothesis/` 和 `.import_linter_cache/`。

### D10 约定写在哪里

design.md 归档以后就不常被看到了，所以需要长期遵守的约定写进两个地方。

**`openspec/config.yaml` 的 `context`**：后续每个变更生成文档时都会读到。补充这些内容：

- 模块内部的文件清单（含 `config.py`）和分层以 D2 为准；存储接口放 ports，repo 放本模块的表和这个接口的数据库实现，service 不直接用 repo
- 包根和层级包的 `__init__.py` 只放 docstring
- 模块之外只能 import 模块的包根
- 包级和模块级都不能有循环依赖，跨模块流程放进 workflows
- 纯区域的 import 白名单和禁用的内置函数，以及扩充的方式
- 任何计算都不能依赖本机时区；`time` 模块全项目禁用
- domain 可以接收 `shared` 中纯计算、无 I/O 的协议（例如交易日历），但不接收 Clock，"现在"作为参数传入
- 测试目录镜像 `src/`；测试替身放 `tests/fakes/`；回放模式用的实现属于正式代码，放 `integrations/`

**README**：给人看的入口。写项目一句话简介、目录分层图、常用命令（`uv sync` 以及五项检查），并指向 `docs/` 和 `openspec/`。

关于"domain 能不能接收日历协议"：

- 考虑过：domain 只接收值，由 service 先把日期算好再传入
  - 没有采用：像"复查日默认 5 个交易日、范围 3 到 20 个交易日"这样的规则会被拆成两半，一半在 service，一半在 domain
- 采用的做法：日历是确定性计算，可以进 domain；Clock 代表外部的"现在"，不能进 domain

### D11 依赖版本

开发依赖用 `uv add --dev` 加入：import-linter（当前 2.15）、grimp（当前 3.17）、hypothesis（当前 6.168.x）。版本由 `uv.lock` 锁定，CI 用 `--locked` 安装。运行依赖本次不变。

## Risks / Trade-offs

- [白名单和禁用名单太严，写 domain 时经常碰壁] -> 有意为之，扩充只需要在变更里改一处常量并写明理由。第一个业务变更时观察碰壁的频率，必要时调整
- [`exhaustive` 不允许模块里出现其他文件] -> 有意为之，避免杂物文件。需要新的文件种类时修改契约
- [检查规则写错，或者日后被改弱，形同虚设] -> 每条规则（包括 import-linter 契约）都有基于真实源码样例、持续运行的负例测试，见 D4
- [TYPE_CHECKING 里的 import 和函数内 import 也算依赖，比一般项目严格] -> 有意为之。纯区域即使只为类型标注，也不应该依赖 I/O 包；只为类型标注而形成的环，同样说明边界不清
- [静态检查拦不住 `getattr` 等动态写法] -> 接受。纯区域有 import 白名单兜底（`time`、`random`、`builtins`、`importlib` 都不在白名单里）；时区问题有动态守卫兜底
- [动态时区守卫只能发现有测试覆盖、并且断言了日期或时间的代码] -> 规则代码本来就要求先写测试；测试里的期望值一律用显式时区写出
- [集合的遍历顺序受 PYTHONHASHSEED 影响，同一输入在不同进程里顺序可能不同] -> 需要固定顺序时用 `sorted()`；接线阶段在运行环境里固定 PYTHONHASHSEED，保证工作流在新进程里恢复时行为一致
- [模块包根导出 service 和 repo 后，单测 domain 会顺带加载数据库等依赖] -> 内核阶段还没有 service 和 repo，不受影响；接线阶段再评估包根是否改成按需导入
- [工具升级后行为或配置格式变化] -> 版本由 `uv.lock` 锁定；升级时重跑全部检查
- [入口输出从问候语变为版本号] -> 只影响占位代码，没有使用方

## Migration Plan

不涉及部署和数据迁移，一次提交完成；需要回滚时直接 revert。

## Open Questions

- Hypothesis `ci` 配置里 `max_examples=200` 等参数，等有了真实的性质测试后再按 CI 耗时调整，不影响本次结构
