"""ID 生成端口：全系统新 ID 的来源。"""

from typing import Protocol
from uuid import UUID


class IdGenerator(Protocol):
    """新 ID 的来源。和 Clock 一样由 bootstrap 注入：回放时换成能重现的实现。"""

    def new_id(self) -> UUID:
        """一个新的 UUID，和之前给出的都不同。"""
        ...
