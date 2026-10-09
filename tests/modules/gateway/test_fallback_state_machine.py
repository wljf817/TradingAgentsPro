"""备用模型的状态机性质测试（add-model-fallback 的 design D13）。

两个案件；PM（p-1、p-2、p-3）、Reviewer（只有 p-1，和 PM 共用主模型）、News（s-1、s-2，带截止
时间）。随机交错开始调用、报告失败（随机的调用错误）、报告成功、拨动时间（包括拨到熔断时间窗口、冷却
结束和截止时间的边界）和结束案件。配置用较小的值：重试 1 次，连续 2 次不可用就冷却，时间窗口 10
分钟，冷却 5 分钟。

参考模型按 spec「熔断」的状态表记每个模型的熔断状态，按「案件内不切回」记每个案件每个 agent 的起点，
每一步按「切换顺序」「尝试的超时」推出期望的下一步。每一步之后检查：起点只往后移，结束案件以后回到
主模型；每次调用里同一个模型的尝试不超过重试次数加 1，模型的位置只往后走、不早于开始时的起点；换到的
模型在那一刻不在冷却中；子 agent 的尝试超时大于 0、不超过离截止时间还剩的时间，到了截止时间只会是
停止；还有可用的模型、冷却中的模型和参考模型一致；全部不可用的说明按先后列出起点以后的每个模型，各
一次。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from tests.fakes.clock import FakeClock
from tests.modules.gateway.builders import BASELINE_MODELS, NOW
from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.model import ModelRef, ModelUse
from tradingagentspro.modules.gateway import (
    AllUnavailable,
    Attempt,
    CallError,
    CallStep,
    DeadlineReached,
    GatewayConfig,
    ModelCall,
    ModelFallbackService,
    ModelSwitch,
    Unavailable,
)

CONFIG = GatewayConfig(call_retries=1, breaker_failures=2)
CASES = (UUID(int=0xC001), UUID(int=0xC002))
AGENTS = (Agent.PM, Agent.REVIEWER, Agent.NEWS)
CHAINS = {agent: BASELINE_MODELS.of(agent).chain for agent in AGENTS}
MODELS = tuple(dict.fromkeys(model for agent in AGENTS for model in CHAINS[agent]))


@dataclass
class _RefBreaker:
    """参考模型里一个模型的熔断状态，按 spec「熔断」的表：until 为 None 时正常，现在早于 until 时
    冷却中，否则冷却结束。
    """

    failures: list[datetime] = field(default_factory=list)  # 正常时，上次成功以来连续的不可用
    until: datetime | None = None

    def cooling(self, now: datetime) -> bool:
        return self.until is not None and now < self.until


@dataclass(eq=False)
class _Running:
    """一次进行中的调用：最新的 ModelCall、开始时的起点、每个模型尝试过几次。按对象区分：并行的两次
    调用可能完全相同。
    """

    call: ModelCall
    first: int
    tries: dict[ModelRef, int] = field(default_factory=dict)


class ModelFallbackMachine(RuleBasedStateMachine):
    """参考模型只按 spec 写，检查时只读 service 的公开方法和返回的步骤。"""

    def __init__(self) -> None:
        super().__init__()
        self.clock = FakeClock(NOW)
        self.service = ModelFallbackService(self.clock, BASELINE_MODELS, CONFIG)
        self.breakers = {model: _RefBreaker() for model in MODELS}
        self.starts: dict[tuple[UUID, Agent], int] = {}
        self.seen_starts: dict[tuple[UUID, Agent], int] = {}  # 上一步之后看到的起点
        self.running: list[_Running] = []

    @property
    def now(self) -> datetime:
        return self.clock.now()

    # 参考模型

    def _ref_failed(self, model: ModelRef) -> None:
        """spec「熔断」表里"不可用"的几行。"""
        breaker, now = self.breakers[model], self.now
        if breaker.until is not None:
            if breaker.cooling(now):
                event("冷却中收到不可用：冷却结束的时刻不变")
                return
            breaker.until = now + CONFIG.breaker_cooldown
            event("冷却结束以后又不可用：再冷却")
            return
        breaker.failures.append(now)
        recent = breaker.failures[-CONFIG.breaker_failures :]
        if len(recent) == CONFIG.breaker_failures and now - recent[0] <= CONFIG.breaker_window:
            breaker.failures, breaker.until = [], now + CONFIG.breaker_cooldown
            event("连续不可用：冷却")
        elif len(recent) == CONFIG.breaker_failures:
            event("连续不可用但超过时间窗口：不冷却")
        if len(recent) == CONFIG.breaker_failures and now - recent[0] == CONFIG.breaker_window:
            event("最早一次到这次正好是时间窗口")

    def _ref_succeeded(self, model: ModelRef) -> None:
        breaker = self.breakers[model]
        if breaker.cooling(self.now):
            event("冷却中收到成功：恢复正常")
        elif breaker.until is not None:
            event("冷却结束以后成功：恢复正常")
        breaker.failures, breaker.until = [], None

    def _next_from(self, chain: tuple[ModelRef, ...], index: int) -> int | None:
        """index 起第一个不在冷却中的位置；后面都在冷却中时为 None。"""
        return next(
            (i for i in range(index, len(chain)) if not self.breakers[chain[i]].cooling(self.now)),
            None,
        )

    def _timeout(self, agent: Agent, deadline: datetime | None) -> timedelta:
        if agent is not Agent.NEWS:
            return CONFIG.call_timeout
        assert deadline is not None
        return min(CONFIG.subagent_call_timeout, deadline - self.now)

    def _check_moved(
        self, step: CallStep, call: ModelCall, first: int, error: CallError | None
    ) -> None:
        """按「切换顺序」从 call 往后换模型的期望。error 为 None 时是开始调用，从起点 call.index 起
        跳过冷却中的模型；否则 call.model 刚刚不可用、原因是 error，从下一个起跳过冷却中的模型。
        first 是开始时的起点。每越过一个模型一个切换的事件，最后一个模型后面没有。
        """
        chain, now = call.chain, self.now
        skip_from = call.index if error is None else call.index + 1
        target = self._next_from(chain, skip_from)
        stop = len(chain) if target is None else target
        failed = () if error is None else (Unavailable(call.model, error),)
        skipped = tuple(Unavailable(chain[i], None) for i in range(skip_from, stop))
        unavailable = (*call.unavailable, *failed, *skipped)
        departed = ([] if error is None else [call.index]) + list(range(skip_from, stop))
        switches = tuple(
            ModelSwitch(
                call.case_id,
                call.agent,
                chain[j],
                chain[j + 1],
                error if j == call.index else None,
                now,
            )
            for j in departed
            if j + 1 < len(chain)
        )
        if skipped:
            event("跳过冷却中的模型")
        if target is None:
            assert step == AllUnavailable(call.case_id, call.agent, unavailable, switches)
            assert [u.model for u in step.unavailable] == list(chain[first:])
            parts = step.detail.removeprefix("模型都不可用：").split("；")
            assert [part.split(" ")[0] for part in parts] == [str(m) for m in chain[first:]]
            event("全部不可用")
            return
        expected = ModelCall(call.case_id, call.agent, chain, target, 1, call.deadline, unavailable)
        assert step == Attempt(expected, self._timeout(call.agent, call.deadline), switches)
        assert step.timeout > timedelta(0)
        assert not self.breakers[chain[target]].cooling(now), "换到的模型不在冷却中"
        if switches:
            event("切换")

    # 开始调用

    @rule(
        case=st.sampled_from(CASES),
        agent=st.sampled_from(AGENTS),
        left=st.sampled_from([0, 1, 30, 60, 61, 299, 300]),
        copies=st.integers(1, 2),
    )
    def begin(self, case: UUID, agent: Agent, left: int, copies: int) -> None:
        """开始 copies 次调用：2 次是同一个案件同一个 agent 并行的调用，比如 PM 一次派给 News 的两个
        任务。News 的截止时间是现在加 left 秒。
        """
        deadline = self.now + timedelta(seconds=left) if agent is Agent.NEWS else None
        first = self.starts.get((case, agent), 0)
        call = ModelCall(case, agent, CHAINS[agent], first, 1, deadline)
        for _ in range(copies):
            step = self.service.begin(case, agent, deadline)

            if deadline is not None and self.now >= deadline:
                assert step == DeadlineReached(call)
                event("开始时已经到了截止时间：停止")
                continue
            self._check_moved(step, call, first, None)
            if first > 0:
                event("从备用模型的起点开始")
            if isinstance(step, Attempt):
                self.running.append(_Running(step.call, first, {step.call.model: 1}))

    # 报告失败和成功

    @rule(data=st.data(), error=st.sampled_from(CallError))
    def fail(self, data: st.DataObject, error: CallError) -> None:
        """一次尝试失败。"""
        if self.running:
            self._fail_once(data.draw(st.sampled_from(self.running)), error)

    @rule(data=st.data(), error=st.sampled_from(CallError))
    def make_unavailable(self, data: st.DataObject, error: CallError) -> None:
        """spec 里的"某模型不可用"：一次调用正在尝试的模型连续失败，直到换了模型或者调用结束。"""
        if not self.running:
            return
        running: _Running | None = data.draw(st.sampled_from(self.running))
        assert running is not None
        model = running.call.model
        while running is not None and running.call.model == model:
            running = self._fail_once(running, error)

    def _fail_once(self, running: _Running, error: CallError) -> _Running | None:
        """报告 running 正在进行的尝试失败，按参考模型检查下一步；返回之后还在进行的调用。"""
        call = running.call
        last = call.attempt > CONFIG.call_retries

        step = self.service.fail(call, error)

        self.running.remove(running)
        if last:
            self._ref_failed(call.model)
        if call.deadline is not None and self.now >= call.deadline:
            assert step == DeadlineReached(call), "到了截止时间只会是停止"
            event("到了截止时间：停止" + ("（模型已不可用）" if last else ""))
            return None
        if not last:
            retry = ModelCall(
                call.case_id,
                call.agent,
                call.chain,
                call.index,
                call.attempt + 1,
                call.deadline,
                call.unavailable,
            )
            assert step == Attempt(retry, self._timeout(call.agent, call.deadline))
            assert step.timeout > timedelta(0)
            event("重试")
        else:
            self._check_moved(step, call, running.first, error)
        if not isinstance(step, Attempt):
            return None
        model = step.call.model
        running.tries[model] = running.tries.get(model, 0) + 1
        assert step.call.index >= call.index, "模型的位置只往后走"
        assert running.tries[model] <= CONFIG.call_retries + 1
        following = _Running(step.call, running.first, running.tries)
        self.running.append(following)
        return following

    @rule(data=st.data())
    def succeed(self, data: st.DataObject) -> None:
        if not self.running:
            return
        running = data.draw(st.sampled_from(self.running))
        call = running.call
        key = (call.case_id, call.agent)

        use = self.service.succeed(call)

        self.running.remove(running)
        assert use == ModelUse(model=call.model, fallback=call.index > 0)
        self._ref_succeeded(call.model)
        before = self.starts.get(key, 0)
        if call.index > before:
            event("起点往后移")
        elif call.index < before:
            event("并行的调用后成功、用的是起点前面的模型：起点不往回移")
        self.starts[key] = max(before, call.index)

    # 结束案件和拨动时间

    @rule(case=st.sampled_from(CASES))
    def end_case(self, case: UUID) -> None:
        """案件结束；这个案件进行中的调用不再报告（案件结束以后才报告的情况按 design 省略）。"""
        self.service.end_case(case)

        if any(key[0] == case and index > 0 for key, index in self.starts.items()):
            event("结束起点在备用模型的案件")
        for key in [key for key in self.starts if key[0] == case]:
            del self.starts[key]
            self.seen_starts[key] = 0
        self.running = [r for r in self.running if r.call.case_id != case]

    @rule(seconds=st.sampled_from([1, 30, 59, 60, 299, 300, 301, 599, 600, 601]))
    def advance(self, seconds: int) -> None:
        """往后拨。拨的秒数从少数几个值里取，几次拨动加起来常常正好是冷却时间或时间窗口。"""
        self.clock.advance(timedelta(seconds=seconds))

    @rule(data=st.data(), offset=st.sampled_from([-1, 0, 1]))
    def to_a_boundary(self, data: st.DataObject, offset: int) -> None:
        """拨到某个边界或它的前后一秒，只往后拨：最近一次不可用加时间窗口、冷却结束、截止时间。"""
        moments = [
            b.failures[-1] + CONFIG.breaker_window for b in self.breakers.values() if b.failures
        ]
        moments += [b.until for b in self.breakers.values() if b.until is not None]
        moments += [r.call.deadline for r in self.running if r.call.deadline is not None]
        if not moments:
            return
        target = data.draw(st.sampled_from(moments)) + timedelta(seconds=offset)
        if target > self.now:
            self.clock.set(target)

    # 不变量

    @invariant()
    def starts_only_move_forward(self) -> None:
        """起点和参考模型一致，只往后移；结束案件以后回到主模型。"""
        for case in CASES:
            for agent in AGENTS:
                key = (case, agent)
                index = self.starts.get(key, 0)
                assert self.service.case_model(case, agent) == ModelUse(
                    model=CHAINS[agent][index], fallback=index > 0
                )
                assert index >= self.seen_starts.get(key, 0), "起点只往后移"
                self.seen_starts[key] = index

    @invariant()
    def running_calls_are_well_formed(self) -> None:
        """进行中的调用：尝试次数不超过重试次数加 1，位置不早于开始时的起点。"""
        for running in self.running:
            call = running.call
            assert 1 <= call.attempt <= CONFIG.call_retries + 1
            assert running.first <= call.index < len(call.chain)
            assert [u.model for u in call.unavailable] == list(
                call.chain[running.first : call.index]
            )

    @invariant()
    def availability_matches_the_reference(self) -> None:
        """还有可用的模型、冷却中的模型和参考模型一致。"""
        now = self.now
        for agent in AGENTS:
            expected = any(not self.breakers[m].cooling(now) for m in CHAINS[agent])
            assert self.service.has_available_model(agent) is expected
            if not expected:
                event(f"{agent.value} 没有可用的模型")
        assert self.service.cooling() == {
            model: b.until
            for model, b in self.breakers.items()
            if b.until is not None and b.cooling(now)
        }


TestModelFallbackMachine = ModelFallbackMachine.TestCase
