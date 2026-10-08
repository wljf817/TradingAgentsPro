"""测试用的一次 /plan：PlanCommand 的测试不连 Discord，用它记下应答和回复的先后。

见 add-manual-plan-entry 的 design D11。
"""

import threading
from dataclasses import dataclass, field


@dataclass
class FakeCall:
    """满足 CommandCall。refuse、defer、reply 按顺序记进 log，log 可以和 FakePlanIntake 共用。

    - fail_defer、fail_reply：设为真时，defer、reply 记下以后抛错（模拟 Discord 报错）
    - replied：reply 被调用以后 set，用来等别的线程里的命令回复
    """

    user_id: int
    log: list[str] = field(default_factory=list)
    fail_defer: bool = False
    fail_reply: bool = False
    replied: threading.Event = field(default_factory=threading.Event)

    async def refuse(self, text: str) -> None:
        self.log.append(f"refuse:{text}")

    async def defer(self) -> None:
        self.log.append("defer")
        if self.fail_defer:
            raise RuntimeError("Discord 对应答报错")

    async def reply(self, text: str) -> None:
        self.log.append(f"reply:{text}")
        self.replied.set()
        if self.fail_reply:
            raise RuntimeError("Discord 对回复报错")

    @property
    def replies(self) -> list[str]:
        return [entry.removeprefix("reply:") for entry in self.log if entry.startswith("reply:")]
