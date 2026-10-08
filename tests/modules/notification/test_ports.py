"""通知模块的端口：测试替身满足端口，mypy 检查。"""

from tests.fakes.plan_intake import FakePlanIntake
from tradingagentspro.modules.notification import PlanIntake


def test_fake_plan_intake_satisfies_the_port() -> None:
    intake: PlanIntake = FakePlanIntake({"AAPL": "科技"})

    assert intake.held_sector("AAPL") == "科技"
    assert intake.held_sector("MSFT") is None
