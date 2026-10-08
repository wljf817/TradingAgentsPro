"""随机 ID：全项目唯一可以调用 uuid4 的地方。"""

from uuid import UUID, uuid4


class RandomIdGenerator:
    """满足 IdGenerator：随机 UUID（版本 4）。"""

    def new_id(self) -> UUID:
        return uuid4()
