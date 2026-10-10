"""测试用的收盘对账记录存储：存在内存里，按交易日保存。"""

from datetime import date

from tradingagentspro.modules.execution import CloseReconciliation


class InMemoryCloseReconciliationStore:
    """满足执行模块的 CloseReconciliationStore。用同一个实例重新创建执行用例，相当于进程重启。

    - saved：每次真正写入的记录，按顺序；内容和最新的相同时不写，和数据库实现一样
    - fail_next_save：设为真时，下一次保存抛 RuntimeError（数据库出错），之后恢复正常
    """

    def __init__(self) -> None:
        self._records: dict[date, CloseReconciliation] = {}
        self.saved: list[CloseReconciliation] = []
        self.fail_next_save = False

    def get(self, day: date) -> CloseReconciliation | None:
        return self._records.get(day)

    def save(self, record: CloseReconciliation) -> None:
        if self.fail_next_save:
            self.fail_next_save = False
            raise RuntimeError("测试安排的数据库错误")
        if self._records.get(record.day) == record:
            return
        self._records[record.day] = record
        self.saved.append(record)
