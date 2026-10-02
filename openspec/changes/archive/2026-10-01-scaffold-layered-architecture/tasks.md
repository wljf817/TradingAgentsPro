# Tasks

## 1. 依赖与 Ruff 规则

- [x] 1.1 加入开发依赖：运行 `uv add --dev import-linter grimp hypothesis`（design D11）。验证：`uv sync --locked` 成功，`uv run lint-imports --help` 能运行，`uv run python -c "import grimp, hypothesis"` 不报错
- [x] 1.2 按 design D7 修改 `pyproject.toml` 的 Ruff 配置：
  - `select` 增加 `DTZ`、`TID`、`RUF`
  - 配置 `allowed-confusables`
  - 配置 `banned-api`：禁用整个 `time` 模块、`datetime` 里四个读取当前时间的方法、`uuid.uuid1` 和 `uuid.uuid4`，每项写明应改用什么

  验证：
  - 现有代码上 `uv run ruff check` 通过
  - 临时在 `src/` 下建一个探针文件，依次写入：`import time`、`datetime.now(UTC)`、`uuid.uuid4()`、`Decimal(0.1)`、不带时区的 `datetime(...)`、含中文标点的字符串、含西里尔字母的字符串。确认：
    - 前三项报 TID251
    - `Decimal(0.1)` 报 RUF032，不带时区的 datetime 报 DTZ001
    - 西里尔字母报 RUF001，中文标点不报
  - 确认后删除探针
- [x] 1.3 `.gitignore` 增加 `.hypothesis/` 和 `.import_linter_cache/`。验证：`git check-ignore .hypothesis/ .import_linter_cache/` 输出两行（路径结尾要带 `/`：目录还不存在时，git 只有看到结尾的 `/` 才按目录匹配）

## 2. 层级包、入口和测试目录

- [x] 2.1 建测试目录骨架和时区守卫（design D7、D8）：
  - 建 `tests/__init__.py`、`tests/architecture/__init__.py`、`tests/bootstrap/__init__.py`、`tests/fakes/__init__.py`
  - 先写 `tests/test_timezone_guard.py`：用一个固定的 2026 年时间戳，断言本地时区偏移是 +14 小时。运行它，确认因为本机不是 UTC+14 而失败
  - 写 `tests/conftest.py`：
    - 测试开始前把进程时区设为 `Pacific/Kiritimati`，并调用 `time.tzset()`
    - 注册 Hypothesis 的 `dev` 和 `ci` 两套配置，用 `HYPOTHESIS_PROFILE` 选择，默认 `dev`
  - 在 Ruff 的 `per-file-ignores` 里只为 `tests/conftest.py` 放行 `TID251`
  - CI 的 Pytest 步骤加环境变量 `HYPOTHESIS_PROFILE: ci`

  验证：
  - `tests/test_timezone_guard.py` 通过
  - `uv run pytest -v` 头部显示 `hypothesis profile 'dev'`（Hypothesis 只在 `-v` 时打印这一行）
  - `HYPOTHESIS_PROFILE=ci uv run pytest -v` 头部显示 `hypothesis profile 'ci'`
  - `uv run ruff check` 通过
- [x] 2.2 先写入口测试 `tests/bootstrap/test_cli.py`：调用 `tradingagentspro.bootstrap.cli.main`，断言输出为 `tradingagentspro <版本号>`；版本号用 `importlib.metadata.version("tradingagentspro")` 读取。验证：运行这个测试，确认因为 `bootstrap` 还不存在而失败
- [x] 2.3 建骨架和入口（design D1）：
  - 建 6 个层级包 `shared`、`contracts`、`modules`、`integrations`、`workflows`、`bootstrap`。每个包的 `__init__.py` 只放 docstring，写明这一层放什么、能依赖谁
  - 包根 `__init__.py` 也只保留 docstring
  - 新建 `bootstrap/cli.py`，实现 `main`
  - 把 `[project.scripts]` 改为 `tradingagentspro.bootstrap.cli:main`
  - 删除 `tests/test_smoke.py`

  验证：
  - 2.2 的测试通过
  - `uv run tradingagentspro` 输出 `tradingagentspro 0.1.0`
  - `uv run mypy`、`uv run ruff check`、`uv run ruff format --check` 都通过
- [x] 2.4 写 README 第一版：
  - 项目一句话简介
  - 目录分层图，写明每层放什么
  - 常用命令：`uv sync`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run pytest`

  在 `openspec/config.yaml` 的 `context` 补充两条约定（design D10）：
  - 测试目录镜像 `src/`；测试替身放 `tests/fakes/`；回放模式用的实现放 `integrations/`
  - 任何计算都不能依赖本机时区；`time` 模块全项目禁用

  验证：
  - README 里的每条命令照抄都能运行成功
  - `openspec instructions design --change scaffold-layered-architecture --json` 返回的 `context` 里有新加的约定

## 3. import-linter 契约

- [x] 3.1 写样例包工具 `tests/architecture/sample_package.py`（design D4）：在临时目录里按"相对路径 -> 源码"生成一个小包，并把临时目录放进 `sys.path`；每个样例用独立的临时目录。同时写一个小测试。验证：用工具生成的样例包，grimp 读出的模块列表和预期一致
- [x] 3.2 先写契约的负例测试 `tests/architecture/test_import_contracts.py`（design D4）。测试的做法：
  - 读取 `pyproject.toml` 的 `[tool.importlinter]`，把包名换成样例包名，写到临时目录
  - 用 `importlinter.cli.lint_imports` 按契约 `id` 逐个运行

  样例覆盖：
  - 合规的骨架加一个合规的模块：三份契约都通过
  - `workflows` import `integrations`：`top-layers` 报错
  - `domain` import `service`：`module-layers` 报错
  - `service` import `repo`：`module-layers` 报错
  - 模块里多出 `helpers.py`：`module-layers` 报错
  - 两个模块互相 import：`acyclic` 报错

  验证：测试因为 `pyproject.toml` 里还没有契约而失败
- [x] 3.3 在 `pyproject.toml` 写 `[tool.importlinter]`，设 `root_package = "tradingagentspro"`，并加三份带 `id` 的契约：
  - `top-layers`（design D1）：`containers = ["tradingagentspro"]`，`layers = ["bootstrap", "workflows | integrations", "modules", "contracts", "shared"]`
  - `module-layers`（design D2）：`containers = ["tradingagentspro.modules.*"]`，`layers = ["(service) | (repo)", "(ports)", "(domain)", "(config)"]`，`exhaustive = true`
  - `acyclic`（design D3）：`type = "acyclic_siblings"`，`ancestors = ["tradingagentspro"]`

  验证：
  - 3.2 的测试全部通过
  - `uv run lint-imports` 对真实包显示三份契约都是 KEPT
- [x] 3.4 CI 在 Mypy 之后、Pytest 之前加 `uv run lint-imports` 步骤（design D9）。README 的常用命令加上 `uv run lint-imports`，用一句话说明它检查什么。`openspec/config.yaml` 的 `context` 补充两条约定：
  - 模块的文件清单和分层以 design D2 为准，包括 `config.py`。存储接口放在 ports；repo 放本模块的表和这个接口的数据库实现；service 不直接用 repo
  - 包和包之间不能有循环依赖，跨模块的流程放进 workflows

  验证：
  - `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` 不报错
  - `openspec instructions` 返回的 `context` 里有新加的约定

## 4. 架构测试

以下每条规则都按同样的流程做：用 3.1 的样例包工具先写正例和负例测试，运行确认因为规则不存在而失败；再在 `tests/architecture/` 实现规则函数，直到测试通过。每条违规都要带模块名、行号和原因。

- [x] 4.1 规则"模块之外只能 import 模块包根"（design D5）。样例覆盖：
  - 合规：别的模块 import X 的包根；X 内部互相 import
  - 违规：别的模块 import `X.domain`；workflows import `X.service`；integrations import `X.ports`

  验证：测试通过，`uv run mypy` 通过
- [x] 4.2 规则"纯区域只能 import 白名单里的包"（design D6）。样例覆盖：
  - 表中四个区域各自允许和不允许的项目内 import
  - pydantic 在 contracts 和 config 里允许，在 domain 和 shared 里不允许
  - 白名单里的标准库允许；`os`、`time`、`random`、`importlib` 不允许
  - 函数内的延迟 import 和 TYPE_CHECKING 里的 import 同样算

  标准库白名单和各区域的允许范围写成集中的常量，每一项注释理由。验证：测试通过，`uv run mypy` 通过
- [x] 4.3 规则"纯区域禁止使用的内置函数"（design D6，用 AST）。样例覆盖：
  - 违规：domain 里调用 `open()`、`print()`、`input()`；`f = open` 这种只引用不调用的写法；`__import__("os")`；`id(x)`
  - 合规：同样的写法放在 service 里不报

  禁用名单写成集中的常量，每一项注释理由。验证：测试通过，`uv run mypy` 通过
- [x] 4.4 规则"包根和层级包的 `__init__.py` 只有 docstring"（design D1，用 AST）。样例覆盖：
  - 合规：只有 docstring，或者文件为空；模块包（比如 `modules/x/__init__.py`）里的 import
  - 违规：包根 import 某个层级包；层级包里写赋值语句

  验证：测试通过，`uv run mypy` 通过
- [x] 4.5 规则"禁止依赖本机时区的写法"（design D7，用 AST，覆盖整个包）。样例覆盖：
  - 不带参数的 `astimezone()` 报错，`astimezone(UTC)` 不报
  - `replace(tzinfo=None)` 报错，`replace(year=2026)` 不报
  - `fromtimestamp(ts).astimezone()` 报错

  验证：测试通过，`uv run mypy` 通过
- [x] 4.6 规则"模块级没有循环 import"（design D3，用 grimp 加 `graphlib`）。样例覆盖：
  - `domain/__init__ -> b -> a -> domain` 报错，失败信息给出整条环路，以及每一步的模块名和行号
  - 两个文件互相 import 报错
  - 没有环的样例通过

  验证：测试通过，`uv run mypy` 通过
- [x] 4.7 写对真实包的检查测试（design D4）：对 `tradingagentspro` 运行 4.1–4.6 的全部规则，断言违规列表为空；失败信息逐条列出模块名、行号和原因。验证：
  - 测试通过
  - 临时在 `shared/` 下建一个探针文件，写入 `import os`，确认测试失败且信息指向这一行，然后删除探针
- [x] 4.8 `openspec/config.yaml` 的 `context` 补充以下约定（design D10）：
  - 包根和层级包的 `__init__.py` 只放 docstring
  - 模块之外只能 import 模块的包根
  - 模块级也不能有循环 import
  - 纯区域的 import 白名单和禁用的内置函数，以及扩充方式：在变更里修改架构测试的常量，并在 design 里写明理由
  - domain 可以接收 `shared` 中纯计算、无 I/O 的协议（比如交易日历），但不接收 Clock，"现在"作为参数传入

  README 用一句话说明架构测试的位置和作用。验证：`openspec instructions` 返回的 `context` 里有新加的约定

## 5. 整体检查

- [x] 5.1 按 CI 的顺序在本地完整跑一遍：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1 validate --all --strict --no-interactive`。验证：全部通过，并且 `git status` 里没有缓存目录和探针文件残留
