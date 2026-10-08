"""测试用的 ID 生成端口：依次给出 UUID(int=1)、UUID(int=2)……，结果可以预先写在测试里。"""

from uuid import UUID


class FakeIds:
    """满足 IdGenerator。issued 是已经给出的个数，第 n 个是 UUID(int=n)。"""

    def __init__(self) -> None:
        self.issued = 0

    def new_id(self) -> UUID:
        self.issued += 1
        return UUID(int=self.issued)
