"""备份脚本的测试。场景见 openspec 的 deployment spec「备份」「部署的配置项」，做法见
add-deployment 的 design D8。

假的 docker 放在临时目录里、排在 PATH 最前面：把收到的参数记到文件里，输出 DUMP 当作 pg_dump
的结果；设了 FAKE_DOCKER_FAIL 时以 1 退出，当作 PostgreSQL 没在运行。
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from tests.deploy.files import DEPLOY

SCRIPT = DEPLOY / "backup.sh"

FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_LOG"
if [ -n "${FAKE_DOCKER_FAIL:-}" ]; then
    echo "service postgres is not running" >&2
    exit 1
fi
printf DUMP
"""

# 新备份的文件名：开始备份的 UTC 时刻
NEW_NAME = re.compile(r"\d{8}T\d{6}Z\.dump")


def old_backups(directory: Path, count: int) -> list[str]:
    """在 directory 里放 count 个旧备份（2000 年的时刻，比新备份旧），返回文件名，最旧的在前。"""
    directory.mkdir(exist_ok=True)
    names = [f"200001{day:02d}T060000Z.dump" for day in range(1, count + 1)]
    for name in names:
        (directory / name).write_text("OLD")
    return names


def run_backup(
    tmp_path: Path, directory: Path, *, fail: bool = False, script: Path = SCRIPT
) -> subprocess.CompletedProcess[str]:
    """用假的 docker 运行备份脚本，备份写到 directory。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
    }
    if fail:
        env["FAKE_DOCKER_FAIL"] = "1"
    return subprocess.run(
        [str(script), str(directory)], env=env, capture_output=True, text=True, check=False
    )


def dumps(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob("*.dump"))


def test_daily_backup(tmp_path: Path) -> None:
    """「每天备份」：多一个以 UTC 时刻命名的备份，内容来自 pg_dump，导出整个数据库。"""
    directory = tmp_path / "backups"

    result = run_backup(tmp_path, directory)

    assert result.returncode == 0, result.stderr
    [name] = dumps(directory)
    assert NEW_NAME.fullmatch(name)
    assert (directory / name).read_text() == "DUMP"
    assert sorted(path.name for path in directory.iterdir()) == [name]  # 没有留下 incomplete
    arguments = (tmp_path / "docker.log").read_text().split()
    assert arguments[:5] == ["compose", "exec", "-T", "postgres", "pg_dump"]
    assert "--format=custom" in arguments
    assert not [word for word in arguments if word.startswith(("--schema", "-n"))]


def test_more_than_the_retention_count(tmp_path: Path) -> None:
    """「超过保留份数」：15 个旧的加这次的，留下最新的 14 个，删掉最旧的 2 个，别的文件不动。"""
    directory = tmp_path / "backups"
    names = old_backups(directory, 15)
    (directory / "notes.txt").write_text("keep")

    result = run_backup(tmp_path, directory)

    assert result.returncode == 0, result.stderr
    kept = dumps(directory)
    assert len(kept) == 14
    assert kept[:13] == names[2:]
    assert NEW_NAME.fullmatch(kept[13])
    assert (directory / "notes.txt").read_text() == "keep"


def test_fewer_than_the_retention_count(tmp_path: Path) -> None:
    """「不到保留份数」：3 个旧的加这次的，4 个都在。"""
    directory = tmp_path / "backups"
    names = old_backups(directory, 3)

    result = run_backup(tmp_path, directory)

    assert result.returncode == 0, result.stderr
    kept = dumps(directory)
    assert len(kept) == 4
    assert kept[:3] == names


def test_failed_backup_keeps_old_backups(tmp_path: Path) -> None:
    """「备份失败」：退出码不是 0，旧备份都在，没有新的备份文件。"""
    directory = tmp_path / "backups"
    names = old_backups(directory, 15)

    result = run_backup(tmp_path, directory, fail=True)

    assert result.returncode != 0
    assert dumps(directory) == names
    assert all((directory / name).read_text() == "OLD" for name in names)


def test_changed_retention_count(tmp_path: Path) -> None:
    """「改保留份数」：脚本里的保留份数改为 7 时，留下最新的 7 个。"""
    script = tmp_path / "backup.sh"
    text = SCRIPT.read_text()
    assert "KEEP=14" in text
    script.write_text(text.replace("KEEP=14", "KEEP=7"))
    shutil.copymode(SCRIPT, script)
    directory = tmp_path / "backups"
    names = old_backups(directory, 7)

    result = run_backup(tmp_path, directory, script=script)

    assert result.returncode == 0, result.stderr
    kept = dumps(directory)
    assert len(kept) == 7
    assert kept[:6] == names[1:]
