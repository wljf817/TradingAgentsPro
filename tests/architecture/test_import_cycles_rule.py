"""规则"模块级没有循环 import"的正例和负例。"""

from pathlib import Path

import pytest

from tests.architecture.rules import find_import_cycles
from tests.architecture.sample_package import create_sample_package

# import-linter 的 acyclic_siblings 看不到这个环：它经过 domain 的包根。
# 实际 import 时会报 "partially initialized module"。
CYCLE_THROUGH_PACKAGE_ROOT = {
    "modules/alpha/domain/__init__.py": (
        "from samplepkg.modules.alpha.domain.b import B\n\nTHING = 1\n"
    ),
    "modules/alpha/domain/b.py": "from samplepkg.modules.alpha.domain.a import A\n\nB = A\n",
    "modules/alpha/domain/a.py": "from samplepkg.modules.alpha.domain import THING\n\nA = THING\n",
}

CYCLE_BETWEEN_TWO_FILES = {
    "shared/a.py": "from samplepkg.shared import b\n",
    "shared/b.py": "\nfrom samplepkg.shared import a\n",
}


def test_cycle_through_package_root_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(tmp_path, monkeypatch, CYCLE_THROUGH_PACKAGE_ROOT)

    violations = find_import_cycles(sample.import_graph(), sample.name)

    assert [(v.module, v.line, v.reason) for v in violations] == [
        (
            "samplepkg.modules.alpha.domain",
            1,
            "循环 import：samplepkg.modules.alpha.domain:1 -> samplepkg.modules.alpha.domain.b:1"
            " -> samplepkg.modules.alpha.domain.a:1 -> samplepkg.modules.alpha.domain",
        )
    ]


def test_cycle_between_two_files_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(tmp_path, monkeypatch, CYCLE_BETWEEN_TWO_FILES)

    violations = find_import_cycles(sample.import_graph(), sample.name)

    assert [(v.module, v.line, v.reason) for v in violations] == [
        (
            "samplepkg.shared.a",
            1,
            "循环 import：samplepkg.shared.a:1 -> samplepkg.shared.b:2 -> samplepkg.shared.a",
        )
    ]


def test_every_cycle_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = create_sample_package(
        tmp_path, monkeypatch, {**CYCLE_THROUGH_PACKAGE_ROOT, **CYCLE_BETWEEN_TWO_FILES}
    )

    violations = find_import_cycles(sample.import_graph(), sample.name)

    assert [v.module for v in violations] == [
        "samplepkg.modules.alpha.domain",
        "samplepkg.shared.a",
    ]


def test_acyclic_package_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "modules/alpha/__init__.py": "from samplepkg.modules.alpha.service import run\n",
            "modules/alpha/service.py": "from samplepkg.modules.alpha.domain import rules\n",
            "modules/alpha/domain/rules.py": "from samplepkg.modules.alpha import config\n",
            "modules/alpha/config.py": "",
            "workflows/flow.py": "from samplepkg.modules import alpha\n",
        },
    )

    assert find_import_cycles(sample.import_graph(), sample.name) == []
