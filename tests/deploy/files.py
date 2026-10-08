"""读仓库根目录 deploy/ 下的部署文件：路径、systemd 单元和环境变量模板的解析。

见 add-deployment 的 design D13。单元文件不写续行，一行一个设置。
"""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SYSTEMD = DEPLOY / "systemd"

# 部署机器上环境变量文件所在的目录；模板是 deploy/<文件名>.example
ENV_DIR = "/etc/tradingagentspro/"

# 节 → 键 → 值的列表（同一个键可以写多行）
Unit = dict[str, dict[str, list[str]]]


def read_unit(name: str) -> Unit:
    """读 deploy/systemd/<name>。"""
    sections: Unit = {}
    current: dict[str, list[str]] = {}
    for raw in (SYSTEMD / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        else:
            key, _, value = line.partition("=")
            current.setdefault(key, []).append(value)
    return sections


def read_env_template(name: str) -> list[tuple[str, str]]:
    """读 deploy/<name>：按顺序列出 (变量名, 值)，跳过注释和空行。"""
    pairs = []
    for raw in (DEPLOY / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        pairs.append((key, value))
    return pairs


def template_for(path: str) -> Path:
    """部署机器上的环境变量文件 /etc/tradingagentspro/<文件名> 在仓库里的模板。"""
    assert path.startswith(ENV_DIR), path
    return DEPLOY / f"{path.removeprefix(ENV_DIR)}.example"
