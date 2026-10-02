"""命令行入口。"""

from importlib.metadata import version


def main() -> None:
    """打印程序名和版本号。"""
    print(f"tradingagentspro {version('tradingagentspro')}")
