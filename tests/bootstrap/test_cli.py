"""命令行入口的测试。"""

from importlib.metadata import version

import pytest

from tradingagentspro.bootstrap.cli import main


def test_main_prints_name_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert capsys.readouterr().out == f"tradingagentspro {version('tradingagentspro')}\n"
