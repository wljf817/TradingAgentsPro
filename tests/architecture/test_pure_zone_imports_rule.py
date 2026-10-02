"""规则"纯区域只能 import 白名单里的包"的正例和负例。"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.architecture.rules import find_impure_imports
from tests.architecture.sample_package import create_sample_package


def test_allowed_imports_in_every_zone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "shared/money.py": "from decimal import Decimal\n\nfrom samplepkg.shared import ids\n",
            "shared/ids.py": "import uuid\n",
            "contracts/intent.py": "import pydantic\n\nfrom samplepkg.shared import money\n",
            "modules/alpha/config.py": (
                "import pydantic\n\nfrom samplepkg.contracts import intent\n"
            ),
            "modules/alpha/domain/model.py": "from dataclasses import dataclass\n",
            "modules/alpha/domain/rules.py": (
                "from __future__ import annotations\n"
                "from collections.abc import Sequence\n"
                "from samplepkg.contracts import intent\n"
                "from samplepkg.modules.alpha import config\n"
                "from samplepkg.modules.alpha.domain import model\n"
                "from samplepkg.shared import money\n"
            ),
            # service 不是纯区域，不受白名单限制。
            "modules/alpha/service.py": "import os\n\nimport dbos\n",
        },
    )

    assert find_impure_imports(sample.import_graph(), sample.name) == []


VIOLATIONS = [
    pytest.param(
        {"shared/x.py": "from samplepkg.contracts import intent\n", "contracts/intent.py": ""},
        "samplepkg.shared.x",
        "samplepkg.contracts.intent",
        id="shared-imports-contracts",
    ),
    pytest.param(
        {
            "contracts/x.py": "from samplepkg.modules import alpha\n",
            "modules/alpha/__init__.py": "",
        },
        "samplepkg.contracts.x",
        "samplepkg.modules.alpha",
        id="contracts-imports-modules",
    ),
    pytest.param(
        {
            "modules/alpha/config.py": "from samplepkg.modules.alpha.domain import rules\n",
            "modules/alpha/domain/rules.py": "",
        },
        "samplepkg.modules.alpha.config",
        "samplepkg.modules.alpha.domain.rules",
        id="config-imports-domain",
    ),
    pytest.param(
        {"modules/alpha/domain/rules.py": "from samplepkg.modules import alpha\n"},
        "samplepkg.modules.alpha.domain.rules",
        "samplepkg.modules.alpha",
        id="domain-imports-own-module-root",
    ),
    pytest.param(
        {
            "modules/alpha/domain/rules.py": "from samplepkg.modules.alpha import ports\n",
            "modules/alpha/ports.py": "",
        },
        "samplepkg.modules.alpha.domain.rules",
        "samplepkg.modules.alpha.ports",
        id="domain-imports-ports",
    ),
    pytest.param(
        {
            "modules/alpha/domain/rules.py": "from samplepkg.modules.beta.domain import rules\n",
            "modules/beta/domain/rules.py": "",
        },
        "samplepkg.modules.alpha.domain.rules",
        "samplepkg.modules.beta.domain.rules",
        id="domain-imports-other-module-domain",
    ),
    pytest.param(
        {"modules/alpha/domain/rules.py": "import pydantic\n"},
        "samplepkg.modules.alpha.domain.rules",
        "pydantic",
        id="pydantic-in-domain",
    ),
    pytest.param(
        {"shared/x.py": "import pydantic\n"},
        "samplepkg.shared.x",
        "pydantic",
        id="pydantic-in-shared",
    ),
    pytest.param(
        {"modules/alpha/domain/rules.py": "import os\n"},
        "samplepkg.modules.alpha.domain.rules",
        "os",
        id="os-in-domain",
    ),
    pytest.param(
        {"modules/alpha/domain/rules.py": "import time\n"},
        "samplepkg.modules.alpha.domain.rules",
        "time",
        id="time-in-domain",
    ),
    pytest.param(
        {"modules/alpha/domain/rules.py": "import random\n"},
        "samplepkg.modules.alpha.domain.rules",
        "random",
        id="random-in-domain",
    ),
    pytest.param(
        {"modules/alpha/domain/rules.py": "import importlib\n"},
        "samplepkg.modules.alpha.domain.rules",
        "importlib",
        id="importlib-in-domain",
    ),
    pytest.param(
        {"modules/alpha/domain/rules.py": "def load() -> None:\n    import os\n"},
        "samplepkg.modules.alpha.domain.rules",
        "os",
        id="lazy-import",
    ),
    pytest.param(
        {
            "modules/alpha/domain/rules.py": (
                "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import os\n"
            )
        },
        "samplepkg.modules.alpha.domain.rules",
        "os",
        id="type-checking-import",
    ),
]


@pytest.mark.parametrize(("files", "importer", "imported"), VIOLATIONS)
def test_disallowed_import_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: Mapping[str, str],
    importer: str,
    imported: str,
) -> None:
    sample = create_sample_package(tmp_path, monkeypatch, files)

    violations = find_impure_imports(sample.import_graph(), sample.name)

    assert [v.module for v in violations] == [importer]
    assert imported in violations[0].reason


def test_violation_points_at_the_import_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {"modules/alpha/domain/rules.py": "from decimal import Decimal\n\nimport os\n"},
    )

    violations = find_impure_imports(sample.import_graph(), sample.name)

    assert [(v.module, v.line) for v in violations] == [("samplepkg.modules.alpha.domain.rules", 3)]
