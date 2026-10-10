"""测试用的止损保护存储：存在内存里，按股票保存最新的记录；给了时钟时还记下历史。"""

from datetime import datetime

from tradingagentspro.modules.execution import Protection
from tradingagentspro.shared.clock import Clock


class InMemoryProtectionStore:
    """满足执行模块的 ProtectionStore。用同一个实例重新创建执行用例，相当于进程重启。

    clock：给了时，每次内容有变化的保存按时钟的现在记下一个版本，history 才能用，
    和数据库实现一样（state-store spec「读取一段时间里的版本」）。
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self._protections: dict[str, Protection] = {}
        self._clock = clock
        self._versions: list[tuple[datetime, Protection]] = []

    def get(self, symbol: str) -> Protection | None:
        return self._protections.get(symbol)

    def active(self) -> tuple[Protection, ...]:
        return tuple(p for p in self._protections.values() if p.is_active)

    def all(self) -> tuple[Protection, ...]:
        return tuple(sorted(self._protections.values(), key=lambda p: p.symbol))

    def history(self, since: datetime) -> tuple[Protection, ...]:
        assert self._clock is not None, "读历史要在构造时给时钟"
        at_since: dict[str, Protection] = {}
        later: list[Protection] = []
        for at, protection in self._versions:
            if at < since:
                at_since[protection.symbol] = protection
            else:
                later.append(protection)
        used = list(enumerate([*at_since.values(), *later]))
        return tuple(p for _, p in sorted(used, key=lambda item: (item[1].symbol, item[0])))

    def save(self, protection: Protection) -> None:
        if self._protections.get(protection.symbol) == protection:
            return
        self._protections[protection.symbol] = protection
        if self._clock is not None:
            self._versions.append((self._clock.now(), protection))
