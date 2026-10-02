"""规则"包根和层级包的 __init__.py 只有 docstring"的正例和负例。"""

from pathlib import Path

import pytest

from tests.architecture.rules import find_code_in_layer_inits
from tests.architecture.sample_package import create_sample_package


def test_docstring_comment_or_empty_file_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "shared/__init__.py": "# 只有注释\n",
            "workflows/__init__.py": "",
            # 模块包根是公开 API，可以 import。
            "modules/alpha/__init__.py": "from samplepkg.modules.alpha.service import run\n",
            "modules/alpha/service.py": "",
        },
    )

    assert find_code_in_layer_inits(sample.name, sample.directory) == []


def test_code_in_package_root_or_layer_package_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": '"""样例包根。"""\n\nfrom samplepkg import integrations\n',
            "modules/__init__.py": '"""modules 层。"""\n\nREGISTRY: dict[str, str] = {}\n',
            "bootstrap/__init__.py": "def main() -> None:\n    pass\n",
        },
    )

    violations = find_code_in_layer_inits(sample.name, sample.directory)

    assert [(v.module, v.line) for v in violations] == [
        ("samplepkg", 3),
        ("samplepkg.bootstrap", 1),
        ("samplepkg.modules", 3),
    ]
