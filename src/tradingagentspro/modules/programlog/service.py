"""程序日志的用例：追加一条，读最近的一条。见 program-log spec 和 add-program-log 的 design D2。"""

from collections.abc import Collection, Sequence
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from tradingagentspro.modules.programlog.domain.entry import Action, Actor, LogEntry
from tradingagentspro.modules.programlog.ports import ProgramLogStore
from tradingagentspro.shared.clock import Clock


class ProgramLog:
    """程序日志的入口：模块、流程和启动程序都经它写，时间从时钟取。

    写入出错（数据库连不上、表不存在）时原样抛出，不重试、不吞掉，见 spec「写入失败」。
    """

    def __init__(self, store: ProgramLogStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def write(
        self,
        actor: Actor,
        action: Action,
        subject: str,
        detail: str,
        *,
        inputs: Sequence[str] = (),
        outputs: Sequence[str] = (),
        case_id: UUID | None = None,
        duration: timedelta | None = None,
        tokens: int | None = None,
        cost: Decimal | None = None,
    ) -> None:
        """追加一条：时间是现在，其余是给出的值。"""
        self._store.append(
            LogEntry(
                recorded_at=self._clock.now(),
                actor=actor,
                action=action,
                subject=subject,
                detail=detail,
                inputs=tuple(inputs),
                outputs=tuple(outputs),
                case_id=case_id,
                duration=duration,
                tokens=tokens,
                cost=cost,
            )
        )

    def latest(self, actions: Collection[Action]) -> LogEntry | None:
        """这几种动作里最近写入的一条；没有时为 None。"""
        return self._store.latest(actions)
