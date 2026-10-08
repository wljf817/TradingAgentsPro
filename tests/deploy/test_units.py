"""systemd 单元的测试。

场景见 openspec 的 deployment spec「自动重启」「启动顺序」「监听的端口」「OpenD 的登录和行情权限」
「密钥」「备份」「部署的配置项」，做法见 add-deployment 的 design D4 到 D8。
"""

import pytest

from tests.deploy.files import ENV_DIR, SYSTEMD, Unit, read_env_template, read_unit, template_for
from tradingagentspro.integrations.moomoo import MoomooConfig

MAIN = "tradingagentspro.service"
DB = "tradingagentspro-db.service"
OPEND = "tradingagentspro-opend.service"
BACKUP = "tradingagentspro-backup.service"
TIMER = "tradingagentspro-backup.timer"
UNITS = (MAIN, DB, OPEND, BACKUP, TIMER)

APP = "/opt/tradingagentspro/app"
USER = "tradingagentspro"

# 改变「退出码 → 重启」对应关系的设置，主程序的单元里一个都不能有
EXIT_STATUS_OVERRIDES = ("SuccessExitStatus", "RestartPreventExitStatus", "RestartForceExitStatus")


def one(unit: Unit, section: str, key: str) -> str:
    """一个只写了一次的设置。"""
    values = unit[section][key]
    assert len(values) == 1, f"[{section}] {key} 写了 {len(values)} 次"
    return values[0]


def words(unit: Unit, section: str, key: str) -> list[str]:
    """一个设置的全部值，按空格拆开（After=、Wants= 可以写多个）。"""
    return [word for value in unit[section].get(key, []) for word in value.split()]


def seconds(value: str) -> int:
    """systemd 的时长：只用到「30」「30s」「5min」这几种写法。"""
    if value.endswith("min"):
        return int(value.removesuffix("min")) * 60
    return int(value.removesuffix("s"))


def test_unit_files_are_exactly_the_five() -> None:
    assert sorted(path.name for path in SYSTEMD.iterdir()) == sorted(UNITS)


@pytest.mark.parametrize(
    ("name", "target"),
    [
        (MAIN, "multi-user.target"),
        (DB, "multi-user.target"),
        (OPEND, "multi-user.target"),
        (TIMER, "timers.target"),
    ],
)
def test_services_and_the_timer_start_at_boot(name: str, target: str) -> None:
    """「部署机器和服务」：所有服务开机自动启动；备份由定时器在开机后排上。"""
    assert one(read_unit(name), "Install", "WantedBy") == target


def test_backup_service_is_started_only_by_the_timer() -> None:
    """备份服务没有 [Install]：不会被单独启用成开机就备份。"""
    assert "Install" not in read_unit(BACKUP)


# 主程序


def test_main_restarts_on_failure_only() -> None:
    """「自动重启」表的前两行：退出码不是 0 或者崩溃时重启，退出码 0 时不重启。"""
    unit = read_unit(MAIN)

    assert one(unit, "Service", "Restart") == "on-failure"
    assert not [key for key in EXIT_STATUS_OVERRIDES if key in unit["Service"]]


def test_main_restart_interval_is_at_least_30_seconds() -> None:
    """「启动失败以后重启」「部署的配置项」：间隔 30 秒，不能更短（每次启动都重新注册 /plan）。"""
    assert seconds(one(read_unit(MAIN), "Service", "RestartSec")) == 30


def test_main_retries_without_limit() -> None:
    """「启动失败以后重启」：连续失败几十次以后照样重试。"""
    assert one(read_unit(MAIN), "Unit", "StartLimitIntervalSec") == "0"


def test_main_stop_timeout_is_90_seconds() -> None:
    """「停止超时」「部署的配置项」：停止最多等 90 秒，用 SIGTERM（默认的停止信号）。"""
    unit = read_unit(MAIN)

    assert seconds(one(unit, "Service", "TimeoutStopSec")) == 90
    assert "KillSignal" not in unit["Service"]


def test_main_runs_the_installed_command_as_the_service_user() -> None:
    unit = read_unit(MAIN)

    assert one(unit, "Service", "ExecStart") == f"{APP}/.venv/bin/tradingagentspro run"
    assert one(unit, "Service", "User") == USER
    assert one(unit, "Service", "WorkingDirectory") == APP


def test_main_starts_after_the_state_store_but_not_after_opend() -> None:
    """「启动顺序」：等状态库（Wants，等待失败时照样启动），不等 OpenD。"""
    unit = read_unit(MAIN)

    assert DB in words(unit, "Unit", "After")
    assert DB in words(unit, "Unit", "Wants")
    assert DB not in words(unit, "Unit", "Requires")
    for key in ("After", "Wants", "Requires", "BindsTo"):
        assert OPEND not in words(unit, "Unit", key)


def test_main_environment_holds_no_secrets() -> None:
    """「密钥」：单元里只设 PYTHONUNBUFFERED，密钥都在环境变量文件里。"""
    unit = read_unit(MAIN)

    assert unit["Service"]["Environment"] == ["PYTHONUNBUFFERED=1"]
    assert one(unit, "Service", "EnvironmentFile") == f"{ENV_DIR}tradingagentspro.env"


# 状态库


def test_state_store_waits_until_healthy() -> None:
    """「启动顺序」第 1 步：容器启动后等到健康检查通过，最多 300 秒。"""
    unit = read_unit(DB)

    assert one(unit, "Service", "Type") == "oneshot"
    assert one(unit, "Service", "RemainAfterExit") == "yes"
    assert one(unit, "Service", "ExecStart") == "/usr/bin/docker compose up --wait"
    assert seconds(one(unit, "Service", "TimeoutStartSec")) == 300
    assert one(unit, "Service", "WorkingDirectory") == f"{APP}/deploy"


def test_state_store_starts_after_docker() -> None:
    unit = read_unit(DB)

    assert "docker.service" in words(unit, "Unit", "Requires")
    assert "docker.service" in words(unit, "Unit", "After")


# OpenD


def test_opend_restarts_after_any_exit() -> None:
    """「OpenD 退出以后重启」「部署的配置项」：不论退出码，30 秒后重启，不限次数。"""
    unit = read_unit(OPEND)

    assert one(unit, "Service", "Restart") == "always"
    assert seconds(one(unit, "Service", "RestartSec")) == 30
    assert one(unit, "Unit", "StartLimitIntervalSec") == "0"


def test_opend_listens_where_the_program_connects() -> None:
    """「OpenD 的地址和程序一致」：命令行上的地址和端口等于 moomoo 适配器的默认配置。"""
    arguments = one(read_unit(OPEND), "Service", "ExecStart").split()
    config = MoomooConfig()

    assert f"-api_ip={config.host}" in arguments
    assert f"-api_port={config.port}" in arguments
    assert config.host == "127.0.0.1"


def test_opend_logs_in_with_the_remembered_password() -> None:
    """「OpenD 的登录和行情权限」：用记住的密码登录，账号来自环境变量文件，自动要回行情权限。"""
    unit = read_unit(OPEND)
    arguments = one(unit, "Service", "ExecStart").split()
    account = [name for name, _ in read_env_template("opend.env.example")]

    assert arguments[0] == "/opt/tradingagentspro/opend/OpenD"
    assert "-login_account=${MOOMOO_LOGIN_ACCOUNT}" in arguments
    assert account == ["MOOMOO_LOGIN_ACCOUNT"]
    assert "-login_by_remember=1" in arguments
    assert "-auto_hold_quote_right=1" in arguments
    assert "-no_monitor=1" in arguments
    assert not [word for word in arguments if "pwd" in word]
    assert one(unit, "Service", "User") == USER
    assert one(unit, "Service", "EnvironmentFile") == f"{ENV_DIR}opend.env"


# 备份


def test_backup_runs_daily_at_06_utc() -> None:
    """「每天备份」「错过备份时刻」「部署的配置项」：每天 06:00 UTC，错过时开机后补做。"""
    unit = read_unit(TIMER)

    assert one(unit, "Timer", "OnCalendar") == "*-*-* 06:00:00 UTC"
    assert one(unit, "Timer", "Persistent") == "true"


def test_backup_runs_the_script_as_root() -> None:
    """备份要用 docker，以 root 运行；备份写到只有 root 能读的目录。"""
    unit = read_unit(BACKUP)

    assert one(unit, "Service", "Type") == "oneshot"
    assert "User" not in unit["Service"]
    assert one(unit, "Service", "ExecStart") == (
        f"{APP}/deploy/backup.sh /var/backups/tradingagentspro"
    )


# 环境变量文件


@pytest.mark.parametrize("name", UNITS)
def test_environment_files_have_templates(name: str) -> None:
    """「密钥」：单元引用的每个环境变量文件都在 /etc/tradingagentspro/ 下，仓库里有它的模板。"""
    for path in read_unit(name).get("Service", {}).get("EnvironmentFile", []):
        assert path.startswith(ENV_DIR)
        assert template_for(path).is_file(), f"{name} 引用的 {path} 没有模板"
