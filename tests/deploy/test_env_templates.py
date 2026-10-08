"""环境变量模板的测试。场景见 openspec 的 deployment spec「密钥」。"""

import pytest

from tests.deploy.files import read_env_template
from tradingagentspro.bootstrap.settings import VARIABLES

TEMPLATES = (
    "tradingagentspro.env.example",
    "postgres.env.example",
    "opend.env.example",
)


def test_main_template_lists_exactly_the_variables_the_program_reads() -> None:
    """「模板和程序读取的变量一致」：变量名和顺序都等于 bootstrap 的 VARIABLES。"""
    names = tuple(name for name, _ in read_env_template("tradingagentspro.env.example"))

    assert names == VARIABLES, (
        f"模板缺少：{set(VARIABLES) - set(names)}，多出：{set(names) - set(VARIABLES)}"
    )


@pytest.mark.parametrize("template", TEMPLATES)
def test_templates_have_no_values(template: str) -> None:
    """「模板里没有值」：每个变量的等号后面都是空的。"""
    pairs = read_env_template(template)

    assert pairs
    assert all(value == "" for _, value in pairs), pairs


def test_postgres_template_has_only_the_password() -> None:
    """PostgreSQL 的模板只有超级用户的密码，用户名和库名写在 compose 文件里。"""
    assert [name for name, _ in read_env_template("postgres.env.example")] == ["POSTGRES_PASSWORD"]


def test_opend_template_has_only_the_login_account() -> None:
    """OpenD 的模板只有登录账号：密码由 OpenD 记住，不写进任何文件。"""
    names = [name for name, _ in read_env_template("opend.env.example")]

    assert names == ["MOOMOO_LOGIN_ACCOUNT"]
