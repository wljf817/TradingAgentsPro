"""规则"纯区域禁止使用的内置函数"的正例和负例。"""

from pathlib import Path

import pytest

from tests.architecture.rules import find_banned_builtins
from tests.architecture.sample_package import create_sample_package

USES_BANNED_BUILTINS = """\
def load(path: str) -> str:
    print(path)
    text = open(path).read()
    return text + input()


reader = open
module = __import__("os")
key = id(reader)
"""


def test_banned_builtins_in_domain_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path, monkeypatch, {"modules/alpha/domain/rules.py": USES_BANNED_BUILTINS}
    )

    violations = find_banned_builtins(sample.name, sample.directory)

    assert [(v.module, v.line) for v in violations] == [
        ("samplepkg.modules.alpha.domain.rules", line) for line in (2, 3, 4, 7, 8, 9)
    ]
    assert [v.reason.split("：")[0] for v in violations] == [
        "print",
        "open",
        "input",
        "open",
        "__import__",
        "id",
    ]


def test_every_pure_zone_is_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "shared/a.py": "print()\n",
            "contracts/a.py": "eval('1')\n",
            "modules/alpha/config.py": "exit()\n",
            "modules/alpha/domain/a.py": "x = __builtins__\n",
        },
    )

    violations = find_banned_builtins(sample.name, sample.directory)

    assert [v.module for v in violations] == [
        "samplepkg.contracts.a",
        "samplepkg.modules.alpha.config",
        "samplepkg.modules.alpha.domain.a",
        "samplepkg.shared.a",
    ]


def test_same_code_outside_pure_zones_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "modules/alpha/service.py": USES_BANNED_BUILTINS,
            "integrations/store.py": USES_BANNED_BUILTINS,
        },
    )

    assert find_banned_builtins(sample.name, sample.directory) == []


def test_field_and_keyword_named_like_a_builtin_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "modules/alpha/domain/model.py": (
                "from dataclasses import dataclass\n"
                "\n"
                "\n"
                "@dataclass(frozen=True)\n"
                "class Order:\n"
                "    id: str\n"
                "\n"
                "    def label(self) -> str:\n"
                "        return self.id\n"
                "\n"
                "\n"
                "def make() -> Order:\n"
                '    return Order(id="a")\n'
            )
        },
    )

    assert find_banned_builtins(sample.name, sample.directory) == []
