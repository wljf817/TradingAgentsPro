"""系统宪章和角色 prompt：读宪章文件，接上由配置生成的风控限额，见 shared-context spec「系统宪章」、
add-shared-context 的 design D4、D5、pm-calls spec「PM 的角色 prompt」和 sub-agent-calls spec
「子 agent 的角色 prompt」。
"""

import tomllib
from pathlib import Path

from tradingagentspro.contracts.charter import Charter
from tradingagentspro.modules.risk import RiskConfig

CHARTER_FILE = Path(__file__).with_name("charter.toml")
PM_PROMPT_FILE = Path(__file__).with_name("pm_prompt.md")
NEWS_PROMPT_FILE = Path(__file__).with_name("news_prompt.md")
FUNDAMENTALS_PROMPT_FILE = Path(__file__).with_name("fundamentals_prompt.md")


def load_charter(path: Path, risk: RiskConfig) -> Charter:
    """读宪章文件；出错时抛异常，由启动的这一步转成启动失败。"""
    file = Charter.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    return Charter(version=file.version, text=f"{file.text}\n\n风控限额：{risk.model_dump_json()}")


def load_role_prompt(path: Path) -> str:
    """读角色 prompt 文件；出错时抛异常，由启动的这一步转成启动失败。"""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"角色 prompt 是空的：{path}")
    return text
