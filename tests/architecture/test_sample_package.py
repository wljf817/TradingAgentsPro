"""样例包工具本身的测试。"""

from pathlib import Path

import pytest

from tests.architecture.sample_package import SAMPLE_PACKAGE, create_sample_package

LAYERS = {
    f"{SAMPLE_PACKAGE}.{layer}"
    for layer in ("shared", "contracts", "modules", "integrations", "workflows", "bootstrap")
}


def _internal_modules(modules: set[str]) -> set[str]:
    return {m for m in modules if m == SAMPLE_PACKAGE or m.startswith(f"{SAMPLE_PACKAGE}.")}


def test_grimp_reads_the_generated_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = create_sample_package(
        tmp_path, monkeypatch, {"modules/alpha/domain/rules.py": "import decimal\n"}
    )

    graph = sample.import_graph()

    assert _internal_modules(graph.modules) == {
        SAMPLE_PACKAGE,
        *LAYERS,
        "samplepkg.modules.alpha",
        "samplepkg.modules.alpha.domain",
        "samplepkg.modules.alpha.domain.rules",
    }
    assert graph.find_modules_directly_imported_by("samplepkg.modules.alpha.domain.rules") == {
        "decimal"
    }


def test_later_sample_does_not_see_earlier_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_sample_package(tmp_path / "a", monkeypatch, {"modules/alpha/service.py": ""})
    second = create_sample_package(tmp_path / "b", monkeypatch, {"modules/beta/service.py": ""})

    modules = _internal_modules(second.import_graph().modules)

    assert "samplepkg.modules.beta.service" in modules
    assert "samplepkg.modules.alpha.service" not in modules
