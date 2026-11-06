"""config/ 下的文件都不进 git，里面有密钥：不存在时生成，只有所有者能读写。"""

from pathlib import Path


def ensure_private_file(path: Path, text: str) -> bool:
    """文件不存在时写入 text，已有的文件不动；给出这次有没有生成。"""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600)
    path.write_text(text, encoding="utf-8")
    return True
