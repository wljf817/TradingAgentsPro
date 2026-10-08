"""PostgreSQL 的 compose 文件的测试。场景见 openspec 的 deployment spec「监听的端口」「密钥」
「自动重启」，做法见 add-deployment 的 design D4。"""

from typing import Any

import pytest
import yaml

from tests.deploy.files import DEPLOY, ENV_DIR, template_for


@pytest.fixture(scope="module")
def postgres() -> dict[str, Any]:
    compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    assert list(compose["services"]) == ["postgres"], "compose 里只有状态库一个服务"
    service: dict[str, Any] = compose["services"]["postgres"]
    return service


def test_image_is_postgres_18(postgres: dict[str, Any]) -> None:
    """和测试用的 testcontainers 镜像同一个大版本。"""
    assert postgres["image"] == "postgres:18"


def test_ports_are_bound_to_localhost_only(postgres: dict[str, Any]) -> None:
    """「只在本机监听」：每个发布的端口都只绑定 127.0.0.1。"""
    ports = postgres["ports"]

    assert ports == ["127.0.0.1:5432:5432"]
    assert all(str(port).startswith("127.0.0.1:") for port in ports)


def test_data_volume_is_at_the_postgres_18_location(postgres: dict[str, Any]) -> None:
    """18 的镜像把数据目录挪到了 /var/lib/postgresql/18/docker，卷要挂在 /var/lib/postgresql。"""
    assert postgres["volumes"] == ["pgdata:/var/lib/postgresql"]


def test_password_comes_only_from_the_env_file(postgres: dict[str, Any]) -> None:
    """「密钥」：密码不在 compose 文件里，只在部署机器上的环境变量文件里，模板在仓库里。"""
    env_file = postgres["env_file"]

    assert "POSTGRES_PASSWORD" not in postgres["environment"]
    assert env_file == f"{ENV_DIR}postgres.env"
    assert template_for(env_file).is_file()


def test_container_restarts_unless_stopped(postgres: dict[str, Any]) -> None:
    """「PostgreSQL 容器崩溃」：Docker 立即重启；人停下的不重启。"""
    assert postgres["restart"] == "unless-stopped"


def test_health_check_uses_tcp(postgres: dict[str, Any]) -> None:
    """就绪要能在 TCP 上接受连接：第一次初始化时的临时服务器只听 Unix socket。"""
    test = postgres["healthcheck"]["test"]

    assert test[:2] == ["CMD", "pg_isready"]
    assert "--host=127.0.0.1" in test
