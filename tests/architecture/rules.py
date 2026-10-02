"""import-linter 表达不了的架构规则。

每条规则输入包名（以及它的 import 图或源码目录），输出违规列表。
正式检查和样例测试走同一套函数，见 test_project_rules.py 和各条规则的测试。
"""

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from importlib.util import find_spec
from itertools import pairwise
from pathlib import Path

import grimp

# 纯区域（shared、contracts、各模块的 domain 和 config）可以 import 的标准库。
# 不在这里的一律不允许，比如 os、sys、io、pathlib、time、random、secrets、logging、
# asyncio、threading、socket、json、zoneinfo、builtins、importlib：
# 纯逻辑不做 I/O、不写日志、不产生随机数，"现在"作为参数传入。
# 扩充时在变更里改这里，并在那个变更的 design 里写明理由。
STDLIB_ALLOWED_IN_PURE_ZONES: dict[str, str] = {
    "__future__": "只影响编译方式，比如推迟求值的类型标注",
    "abc": "抽象基类",
    "bisect": "有序序列上的二分查找",
    "collections": "deque、Counter 等数据结构，以及 collections.abc 里的抽象类型",
    "dataclasses": "domain 的类型用 frozen dataclass",
    "datetime": "日期和时间类型；读取当前时间的方法由 Ruff 禁止",
    "decimal": "金额、价格、数量一律用 Decimal",
    "enum": "枚举",
    "fractions": "精确分数",
    "functools": "reduce、partial、cache 等纯函数工具",
    "heapq": "堆",
    "itertools": "迭代工具",
    "math": "数学函数",
    "numbers": "数值的抽象类型",
    "operator": "运算符对应的函数",
    "re": "正则表达式",
    "statistics": "统计函数",
    "types": "类型工具，比如 MappingProxyType",
    "typing": "类型标注",
    "uuid": "UUID 类型；生成随机 UUID 的函数由 Ruff 禁止",
}

# 纯区域可以 import 的第三方包，按区域列出。shared 和 domain 不允许任何第三方包：
# domain 的类型用 frozen dataclass，校验放在 contracts 和 config 这两个边界上。
THIRD_PARTY_ALLOWED_IN_PURE_ZONES: dict[str, dict[str, str]] = {
    "shared": {},
    "contracts": {"pydantic": "跨模块传递的模型用 Pydantic 校验"},
    "config": {"pydantic": "阈值配置用 Pydantic 校验"},
    "domain": {},
}

# 纯区域里不能读取的内置名字。内置函数不需要 import，白名单管不到，所以单独检查。
# 只检查读取（包括 f = open 这种不调用的引用）；字段名、关键字参数名不受影响。
BANNED_BUILTINS_IN_PURE_ZONES: dict[str, str] = {
    "open": "做 I/O",
    "input": "做 I/O",
    "print": "做 I/O",
    "breakpoint": "进入调试器，属于 I/O",
    "exec": "动态执行代码，能绕过 import 检查",
    "eval": "动态执行代码，能绕过 import 检查",
    "compile": "动态编译代码，能绕过 import 检查",
    "__import__": "动态 import，能绕过 import 检查",
    "__builtins__": "能拿到任何内置函数，绕过这份名单",
    "exit": "退出进程",
    "quit": "退出进程",
    "id": "结果每次运行都可能不同，回放时无法重现",
    "hash": "字符串的哈希值每次运行都不同，回放时无法重现",
}

# 包根下的 6 个层级包；它们和包根的 __init__.py 只能有 docstring。
LAYER_PACKAGES = ("shared", "contracts", "modules", "integrations", "workflows", "bootstrap")


@dataclass(frozen=True, order=True)
class Violation:
    """一条违规：哪个模块、第几行、为什么。"""

    module: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line} {self.reason}"


def _is_within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _first_line(graph: grimp.ImportGraph, importer: str, imported: str) -> int:
    details = graph.get_import_details(importer=importer, imported=imported)
    return min((detail["line_number"] for detail in details), default=0)


@dataclass(frozen=True)
class _PureZone:
    """纯区域：kind 是区域种类，allowed_internal 是可以 import 的项目内包。"""

    kind: str
    allowed_internal: tuple[str, ...]


def _pure_zone(module: str, package: str) -> _PureZone | None:
    """模块所在的纯区域；不在纯区域里时返回 None。"""
    shared, contracts = f"{package}.shared", f"{package}.contracts"
    if _is_within(module, shared):
        return _PureZone("shared", (shared,))
    if _is_within(module, contracts):
        return _PureZone("contracts", (shared, contracts))

    prefix = f"{package}.modules."
    if not module.startswith(prefix):
        return None
    parts = module.removeprefix(prefix).split(".")
    if len(parts) < 2:
        return None
    owner = f"{package}.modules.{parts[0]}"
    config, domain = f"{owner}.config", f"{owner}.domain"
    if parts[1] == "config":
        return _PureZone("config", (shared, contracts, config))
    if parts[1] == "domain":
        return _PureZone("domain", (shared, contracts, domain, config))
    return None


def _source_files(package: str, directory: Path) -> Iterator[tuple[str, ast.Module]]:
    """包里每个 .py 文件的模块名和语法树。"""
    for path in sorted(directory.rglob("*.py")):
        parts = path.relative_to(directory).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        yield ".".join((package, *parts)), tree


def _imports(graph: grimp.ImportGraph, package: str) -> Iterator[tuple[str, str]]:
    """包里每个模块的每一条直接 import，形式是 (importer, imported)。"""
    for importer in sorted(graph.modules):
        if _is_within(importer, package):
            for imported in sorted(graph.find_modules_directly_imported_by(importer)):
                yield importer, imported


def find_private_module_imports(graph: grimp.ImportGraph, package: str) -> list[Violation]:
    """模块之外的代码只能 import 模块的包根，不能 import 模块内部的子模块。"""
    container = f"{package}.modules"
    if container not in graph.modules:
        return []
    module_packages = sorted(graph.find_children(container))

    violations = []
    for importer, imported in _imports(graph, package):
        for module_package in module_packages:
            if imported.startswith(f"{module_package}.") and not _is_within(
                importer, module_package
            ):
                violations.append(
                    Violation(
                        importer,
                        _first_line(graph, importer, imported),
                        f"import 了 {imported}；模块之外只能 import 模块包根 {module_package}",
                    )
                )
    return sorted(violations)


def find_impure_imports(graph: grimp.ImportGraph, package: str) -> list[Violation]:
    """纯区域直接 import 的东西必须在白名单里，见 design D6 的表。"""
    violations = []
    for importer, imported in _imports(graph, package):
        zone = _pure_zone(importer, package)
        if zone is None:
            continue
        if _is_within(imported, package):
            if any(_is_within(imported, allowed) for allowed in zone.allowed_internal):
                continue
            reason = f"纯区域（{zone.kind}）不能 import 项目内的 {imported}"
        elif (
            imported in STDLIB_ALLOWED_IN_PURE_ZONES
            or imported in THIRD_PARTY_ALLOWED_IN_PURE_ZONES[zone.kind]
        ):
            continue
        elif imported in sys.stdlib_module_names:
            reason = f"标准库 {imported} 不在纯区域白名单里"
        else:
            reason = f"纯区域（{zone.kind}）不能使用第三方包 {imported}"
        violations.append(Violation(importer, _first_line(graph, importer, imported), reason))
    return sorted(violations)


def find_banned_builtins(package: str, directory: Path) -> list[Violation]:
    """纯区域里不能读取 BANNED_BUILTINS_IN_PURE_ZONES 里的内置名字。"""
    violations = []
    for module, tree in _source_files(package, directory):
        if _pure_zone(module, package) is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in BANNED_BUILTINS_IN_PURE_ZONES
            ):
                reason = (
                    f"{node.id}：{BANNED_BUILTINS_IN_PURE_ZONES[node.id]}；"
                    "纯区域不能使用这个内置名字，局部变量和参数也不要用这个名字"
                )
                violations.append(Violation(module, node.lineno, reason))
    return sorted(violations)


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def find_code_in_layer_inits(package: str, directory: Path) -> list[Violation]:
    """包根和层级包的 __init__.py 只能有 docstring：import 任何子模块都会先执行它们。"""
    violations = []
    for name in ("", *LAYER_PACKAGES):
        init = directory / name / "__init__.py"
        if not init.exists():
            continue
        module = f"{package}.{name}" if name else package
        body = ast.parse(init.read_text(encoding="utf-8"), filename=str(init)).body
        if body and _is_docstring(body[0]):
            body = body[1:]
        violations += [
            Violation(
                module,
                statement.lineno,
                "包根和层级包的 __init__.py 只能有 docstring：import 任何子模块都会先执行这里",
            )
            for statement in body
        ]
    return sorted(violations)


def _converts_to_local_time(call: ast.Call) -> bool:
    """astimezone() 不带时区，或者显式传入 None，都会转换到本机时区。"""
    if not call.args and not call.keywords:
        return True
    timezone_args = [*call.args[:1], *(kw.value for kw in call.keywords if kw.arg == "tz")]
    return any(isinstance(arg, ast.Constant) and arg.value is None for arg in timezone_args)


def _drops_timezone(call: ast.Call) -> bool:
    return any(
        kw.arg == "tzinfo" and isinstance(kw.value, ast.Constant) and kw.value.value is None
        for kw in call.keywords
    )


def find_local_timezone_uses(package: str, directory: Path) -> list[Violation]:
    """整个包都不能依赖本机时区：DTZ 规则看不到的写法在这里检查。"""
    violations = []
    for module, tree in _source_files(package, directory):
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr == "astimezone" and _converts_to_local_time(node):
                reason = "astimezone() 没有指定时区，会转换到本机时区；请显式传入时区"
            elif node.func.attr == "replace" and _drops_timezone(node):
                reason = "replace(tzinfo=None) 得到不带时区的时间，之后会按本机时区解释"
            else:
                continue
            violations.append(Violation(module, node.lineno, reason))
    return sorted(violations)


def _describe_cycle(graph: grimp.ImportGraph, cycle: list[str]) -> Violation:
    """cycle 按 import 方向排列，首尾相同。从名字最小的模块开始描述，让结果稳定。"""
    nodes = cycle[:-1]
    start = nodes.index(min(nodes))
    nodes = nodes[start:] + nodes[:start]
    steps = [
        f"{importer}:{_first_line(graph, importer, imported)}"
        for importer, imported in pairwise([*nodes, nodes[0]])
    ]
    return Violation(
        nodes[0],
        _first_line(graph, nodes[0], nodes[1]),
        "循环 import：" + " -> ".join([*steps, nodes[0]]),
    )


def find_import_cycles(graph: grimp.ImportGraph, package: str) -> list[Violation]:
    """单个文件之间不能有循环 import，包括经过包根 __init__.py 的环。

    import-linter 的 acyclic_siblings 不看父包和子模块之间的 import，这里补上。
    """
    dependencies: dict[str, set[str]] = {}
    for importer, imported in _imports(graph, package):
        dependencies.setdefault(importer, set())
        if _is_within(imported, package):
            dependencies[importer].add(imported)

    violations = []
    while True:
        sorter: TopologicalSorter[str] = TopologicalSorter()
        for module in sorted(dependencies):
            sorter.add(module, *sorted(dependencies[module]))
        try:
            sorter.prepare()
        except CycleError as error:
            # graphlib 按"被依赖 -> 依赖方"给出环，反过来就是 import 的方向。
            cycle: list[str] = list(reversed(error.args[1]))
            violations.append(_describe_cycle(graph, cycle))
            # 拆掉环上的一条边，继续找别的环。
            dependencies[cycle[0]].discard(cycle[1])
        else:
            return sorted(violations)


def check_package(package: str) -> list[Violation]:
    """对一个包运行这里的全部规则。新增规则时在这里登记。"""
    spec = find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise ValueError(f"找不到包 {package}")
    directory = Path(next(iter(spec.submodule_search_locations)))
    graph = grimp.build_graph(package, include_external_packages=True, cache_dir=None)
    return [
        *find_private_module_imports(graph, package),
        *find_impure_imports(graph, package),
        *find_import_cycles(graph, package),
        *find_banned_builtins(package, directory),
        *find_code_in_layer_inits(package, directory),
        *find_local_timezone_uses(package, directory),
    ]
