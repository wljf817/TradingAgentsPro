"""规则"禁止依赖本机时区的写法"的正例和负例。"""

from pathlib import Path

import pytest

from tests.architecture.rules import find_local_timezone_uses
from tests.architecture.sample_package import create_sample_package

# 放在 integrations 里：这条规则覆盖整个包，不只纯区域。
CONVERSIONS = """\
from datetime import UTC, datetime


def convert(moment: datetime, ts: float) -> list[object]:
    return [
        moment.astimezone(),
        moment.astimezone(UTC),
        moment.astimezone(None),
        moment.astimezone(tz=None),
        moment.replace(tzinfo=None),
        moment.replace(year=2026),
        datetime.fromtimestamp(ts).astimezone(),
    ]
"""


def test_conversions_to_local_time_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = create_sample_package(tmp_path, monkeypatch, {"integrations/clock.py": CONVERSIONS})

    violations = find_local_timezone_uses(sample.name, sample.directory)

    assert [(v.module, v.line) for v in violations] == [
        ("samplepkg.integrations.clock", line) for line in (6, 8, 9, 10, 12)
    ]
    assert "astimezone" in violations[0].reason
    assert "tzinfo=None" in violations[3].reason


def test_explicit_timezones_are_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = create_sample_package(
        tmp_path,
        monkeypatch,
        {
            "shared/time.py": (
                "from datetime import UTC, datetime\n"
                "\n"
                "\n"
                "def to_utc(moment: datetime) -> datetime:\n"
                "    return moment.astimezone(tz=UTC).replace(microsecond=0)\n"
            )
        },
    )

    assert find_local_timezone_uses(sample.name, sample.directory) == []
