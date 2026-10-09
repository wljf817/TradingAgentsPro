"""跟进记录的测试：状态变化的方法，直接构造记录。

状态变化见 openspec 的 follow-ups spec「跟进的状态」表，记录见「跟进的记录」。
基准数据见 tests/modules/followups/builders.py。
"""

from collections.abc import Callable
from dataclasses import replace

import pytest

from tests.modules.followups.builders import (
    BASELINE_NOW,
    F1,
    K1,
    SCHEDULE_REQUEST,
    follow_up,
)
from tradingagentspro.modules.followups import FollowUp, FollowUpStatus

PENDING, TRIGGERED = FollowUpStatus.PENDING, FollowUpStatus.TRIGGERED
REPLACED, VOIDED = FollowUpStatus.REPLACED, FollowUpStatus.VOIDED

type Change = Callable[[FollowUp], FollowUp]

CHANGES: list[Change] = [FollowUp.triggered, FollowUp.replaced, FollowUp.voided]
CHANGE_IDS = ["到点触发", "被替换", "作废"]


def test_new_follow_up_is_pending() -> None:
    """获批的跟进是待执行。"""
    new = FollowUp(F1, "CCC", SCHEDULE_REQUEST, K1, BASELINE_NOW)

    assert new.status is PENDING


@pytest.mark.parametrize(
    ("change", "status"),
    list(zip(CHANGES, [TRIGGERED, REPLACED, VOIDED], strict=True)),
    ids=CHANGE_IDS,
)
def test_pending_follow_up_settles(change: Change, status: FollowUpStatus) -> None:
    """spec「跟进的状态」表的前三行：待执行变为已触发、被替换、已作废，其他字段不变。"""
    pending = follow_up()

    settled = change(pending)

    assert settled.status is status
    assert settled == replace(pending, status=status)
    assert pending.status is PENDING


@pytest.mark.parametrize("change", CHANGES, ids=CHANGE_IDS)
@pytest.mark.parametrize("status", [TRIGGERED, REPLACED, VOIDED])
def test_settled_follow_up_does_not_change(change: Change, status: FollowUpStatus) -> None:
    """spec「跟进的状态」表的最后一行：已触发、被替换、已作废的跟进不再变化。"""
    with pytest.raises(ValueError):
        change(follow_up(status=status))
