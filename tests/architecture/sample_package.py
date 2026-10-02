"""在临时目录里生成样例包，让架构测试用真实源码验证检查规则。"""

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import grimp
import pytest

SAMPLE_PACKAGE = "samplepkg"

# 合规的最小骨架：包根和 6 个层级包，__init__.py 都只有 docstring。
SKELETON: Mapping[str, str] = {
    "__init__.py": '"""样例包根。"""\n',
    "shared/__init__.py": '"""shared 层。"""\n',
    "contracts/__init__.py": '"""contracts 层。"""\n',
    "modules/__init__.py": '"""modules 层。"""\n',
    "integrations/__init__.py": '"""integrations 层。"""\n',
    "workflows/__init__.py": '"""workflows 层。"""\n',
    "bootstrap/__init__.py": '"""bootstrap 层。"""\n',
}


@dataclass(frozen=True)
class SamplePackage:
    """生成好的样例包：name 是包名，directory 是包所在的目录。"""

    name: str
    directory: Path

    def import_graph(self) -> grimp.ImportGraph:
        """用和正式检查相同的参数构建 import 图：包含外部包，不缓存。"""
        return grimp.build_graph(self.name, include_external_packages=True, cache_dir=None)


def create_sample_package(
    root: Path, monkeypatch: pytest.MonkeyPatch, files: Mapping[str, str]
) -> SamplePackage:
    """在 root 下生成名为 samplepkg 的样例包，并把 root 放到 sys.path 最前面。

    files 的键是相对包目录的路径，值是源码。它和 SKELETON 合并，同一路径以 files 为准；
    缺少 __init__.py 的目录会补一个空文件。每个样例要用自己的 root，互不影响。
    """
    directory = root / SAMPLE_PACKAGE
    for relative_path, source in {**SKELETON, **files}.items():
        path = directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    for package_dir in [directory, *(p for p in directory.rglob("*") if p.is_dir())]:
        (package_dir / "__init__.py").touch(exist_ok=True)

    # 样例包从不被真正 import；保险起见清掉同名的已加载模块，避免读到别的样例。
    for name in list(sys.modules):
        if name == SAMPLE_PACKAGE or name.startswith(f"{SAMPLE_PACKAGE}."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(root))
    return SamplePackage(name=SAMPLE_PACKAGE, directory=directory)
