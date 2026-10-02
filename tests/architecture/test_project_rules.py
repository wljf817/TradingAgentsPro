"""对真实的 tradingagentspro 包运行全部架构规则。"""

from tests.architecture.rules import check_package


def test_project_follows_architecture_rules() -> None:
    violations = check_package("tradingagentspro")

    assert not violations, "违反架构规则：\n" + "\n".join(map(str, violations))
