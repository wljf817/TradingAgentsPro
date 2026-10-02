"""import-linter 契约的负例测试：契约日后被改弱时，这里会失败。

测试读取 pyproject.toml 里真实的 [tool.importlinter] 配置，把包名换成样例包，
再对临时目录里的样例包逐个运行契约。
"""

import json
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from importlinter.cli import lint_imports

from tests.architecture.sample_package import SAMPLE_PACKAGE, create_sample_package

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_IDS = ("top-layers", "module-layers", "acyclic")

# 合规的样例：模块 alpha 用到规定的全部文件，并被 workflows、integrations 通过包根使用。
COMPLIANT: Mapping[str, str] = {
    "modules/alpha/__init__.py": "from samplepkg.modules.alpha.service import run\n",
    "modules/alpha/service.py": (
        "from samplepkg.modules.alpha.config import AlphaConfig\n"
        "from samplepkg.modules.alpha.domain.rules import decide\n"
        "from samplepkg.modules.alpha.ports import Store\n"
    ),
    "modules/alpha/repo.py": (
        "from samplepkg.modules.alpha.domain.rules import decide\n"
        "from samplepkg.modules.alpha.ports import Store\n"
    ),
    "modules/alpha/ports.py": "from samplepkg.modules.alpha.domain.rules import decide\n",
    "modules/alpha/domain/rules.py": "from samplepkg.modules.alpha.config import AlphaConfig\n",
    "modules/alpha/config.py": "",
    "workflows/case_flow.py": "from samplepkg.modules import alpha\n",
    "integrations/store.py": "from samplepkg.modules import alpha\n",
    "bootstrap/cli.py": "from samplepkg import integrations, workflows\n",
}

VIOLATIONS = [
    pytest.param(
        "top-layers",
        {"workflows/case_flow.py": "from samplepkg import integrations\n"},
        id="workflows-imports-integrations",
    ),
    pytest.param(
        "module-layers",
        {"modules/alpha/domain/rules.py": "from samplepkg.modules.alpha import service\n"},
        id="domain-imports-service",
    ),
    pytest.param(
        "module-layers",
        {"modules/alpha/service.py": "from samplepkg.modules.alpha import repo\n"},
        id="service-imports-repo",
    ),
    pytest.param(
        "module-layers",
        {"modules/alpha/helpers.py": ""},
        id="unlisted-file-in-module",
    ),
    pytest.param(
        "acyclic",
        {
            "modules/alpha/service.py": "from samplepkg.modules import beta\n",
            "modules/beta/__init__.py": "from samplepkg.modules import alpha\n",
        },
        id="modules-import-each-other",
    ),
]


def _replace_package(value: object, new_name: str) -> object:
    """把配置里出现的 tradingagentspro 换成样例包名。"""
    if isinstance(value, str):
        return value.replace("tradingagentspro", new_name)
    if isinstance(value, list):
        return [_replace_package(item, new_name) for item in value]
    if isinstance(value, dict):
        return {key: _replace_package(item, new_name) for key, item in value.items()}
    return value


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"样例配置不支持这种值：{value!r}")


def _write_contracts_config(path: Path) -> Path:
    """把 pyproject.toml 里的契约配置改写成针对样例包的配置文件。"""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        project_config = tomllib.load(file)
    config = _replace_package(project_config["tool"]["importlinter"], SAMPLE_PACKAGE)
    assert isinstance(config, dict)

    settings = {key: value for key, value in config.items() if key != "contracts"}
    lines = ["[tool.importlinter]"]
    lines += [f"{key} = {_toml_value(value)}" for key, value in settings.items()]
    for contract in config["contracts"]:
        lines += ["", "[[tool.importlinter.contracts]]"]
        lines += [f"{key} = {_toml_value(value)}" for key, value in contract.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run_contract(config_path: Path, contract_id: str) -> int:
    return lint_imports(
        config_filename=str(config_path), limit_to_contracts=(contract_id,), no_cache=True
    )


def test_compliant_sample_keeps_every_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_sample_package(tmp_path, monkeypatch, COMPLIANT)
    config_path = _write_contracts_config(tmp_path / "importlinter.toml")

    results = {contract_id: _run_contract(config_path, contract_id) for contract_id in CONTRACT_IDS}

    assert results == dict.fromkeys(CONTRACT_IDS, 0)


@pytest.mark.parametrize(("contract_id", "overrides"), VIOLATIONS)
def test_violation_breaks_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contract_id: str,
    overrides: Mapping[str, str],
) -> None:
    create_sample_package(tmp_path, monkeypatch, {**COMPLIANT, **overrides})
    config_path = _write_contracts_config(tmp_path / "importlinter.toml")

    assert _run_contract(config_path, contract_id) == 1
