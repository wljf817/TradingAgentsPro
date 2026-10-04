"""测试用的止损保护存储：存在内存里，按股票保存最新的记录。"""

from tradingagentspro.modules.execution import Protection, StopStatus


class InMemoryProtectionStore:
    """满足执行模块的 ProtectionStore。用同一个实例重新创建执行用例，相当于进程重启。"""

    def __init__(self) -> None:
        self._protections: dict[str, Protection] = {}

    def get(self, symbol: str) -> Protection | None:
        return self._protections.get(symbol)

    def active(self) -> tuple[Protection, ...]:
        return tuple(
            p
            for p in self._protections.values()
            if (p.stop is not None and p.stop.status is not StopStatus.ENDED)
            or p.unprotected_since is not None
        )

    def save(self, protection: Protection) -> None:
        self._protections[protection.symbol] = protection
