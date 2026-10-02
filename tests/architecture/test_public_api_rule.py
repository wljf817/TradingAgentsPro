"""规则"模块之外只能 import 模块包根"的正例和负例。"""

from pathlib import Path

import pytest

from tests.architecture.rules import find_private_module_imports
from tests.architecture.sample_package import create_sample_package


def test_imports_through_package_root_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "modules/alpha/__init__.py": "from samplepkg.modules.alpha.service import run\n",
            "modules/alpha/service.py": (
                "from samplepkg.modules import beta\n"
                "from samplepkg.modules.alpha.domain import rules\n"
            ),
            "modules/alpha/domain/rules.py": "",
            "modules/beta/__init__.py": "",
            "workflows/flow.py": (
                "import samplepkg.modules.beta\nfrom samplepkg.modules import alpha\n"
            ),
        },
    )

    assert find_private_module_imports(sample.import_graph(), sample.name) == []


def test_imports_into_module_internals_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "modules/alpha/domain/rules.py": "",
            "modules/alpha/service.py": "",
            "modules/alpha/ports.py": "",
            "modules/beta/service.py": "\nfrom samplepkg.modules.alpha.domain import rules\n",
            "workflows/flow.py": "from samplepkg.modules.alpha import service\n",
            "integrations/store.py": (
                "import os\n\nfrom samplepkg.modules.alpha.ports import Store\n"
            ),
        },
    )

    violations = find_private_module_imports(sample.import_graph(), sample.name)

    assert [(v.module, v.line) for v in violations] == [
        ("samplepkg.integrations.store", 3),
        ("samplepkg.modules.beta.service", 2),
        ("samplepkg.workflows.flow", 1),
    ]
    assert "samplepkg.modules.alpha.ports" in violations[0].reason
    assert "samplepkg.modules.alpha.domain.rules" in violations[1].reason
    assert "samplepkg.modules.alpha.service" in violations[2].reason
