"""系统宪章：读宪章文件，接上由配置生成的风控限额，见 shared-context spec「系统宪章」和
add-shared-context 的 design D4、D5。
"""

import tomllib
from pathlib import Path

from tradingagentspro.contracts.charter import Charter
from tradingagentspro.modules.risk import RiskConfig

CHARTER_FILE = Path(__file__).with_name("charter.toml")


def load_charter(path: Path, risk: RiskConfig) -> Charter:
    """读宪章文件；出错时抛异常，由启动的这一步转成启动失败。"""
    file = Charter.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    return Charter(version=file.version, text=f"{file.text}\n\n风控限额：{risk.model_dump_json()}")
