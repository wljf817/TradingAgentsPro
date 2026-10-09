"""测试用的费用存储：存在内存里，按写入的先后。"""

from datetime import datetime

from tradingagentspro.modules.gateway import CallCost


class InMemoryCostStore:
    """满足 Agent 网关的 CostStore。只追加：没有修改和删除。"""

    def __init__(self) -> None:
        self._entries: list[CallCost] = []  # 按写入的先后

    def since(self, moment: datetime) -> tuple[CallCost, ...]:
        return tuple(entry for entry in self._entries if entry.recorded_at >= moment)

    def add(self, entry: CallCost) -> None:
        self._entries.append(entry)
