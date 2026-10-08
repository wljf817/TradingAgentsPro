# Tasks

## 1. 完整版 Proposal 补上已决定和新发现的条目

- [ ] 1.1 人同意 proposal「需要人决定」以后，按第 1 到 9 条修改完整版 Proposal，每处只加或改一两句话：
  - 「架构 · 审核层：Critic」：质询 2 轮后仍有分歧、退出类方案有异议时，照常过风控，在人工确认时连同双方意见交给人（第 2 条）；一轮是质询一次、回应一次，回应以后 Critic 再审一次（第 4 条）；退出类方案只审一次，有异议不交 PM 回应，否决按异议处理（第 5 条）
  - 「架构 · 风控与执行」下单前再校验："方案作废并退回 PM"改为方案作废、案件结束并记下原因，PM 在这只股票的下一个案件里看到（第 1 条）
  - 「调度器与运行模块」案件流程第 3 步：结论校验不通过时 PM 在同一个案件里修改再提交，最多重交 2 次，用完按没有结论结束（第 7 条）
  - 案件流程第 4、5 步：补充研究在 Critic 这次审核里完成，不算一轮（第 4 条）；PM 修改只改方案条款，回到 Critic 审核（第 6 条）；"推送给人决定"改为在人工确认时决定（第 2 条）
  - 同一节排队规则：退出类方案"排队的案件等确认结果出来后再运行"改为"等这个案件结束后再运行"（第 9 条）
  - 「风控引擎与执行」："其他不满足规则的情况一律拒绝，退回 PM"，以及下单前再校验和交易时段两处"作废并退回 PM"：改为案件结束并记下原因，不在同一个案件里重来（第 1 条）
  - 「故障处理」：Critic 主备模型全部不可用时，退出方案跳过 Critic、交人确认并注明，开仓方案的案件按系统故障结束（第 3 条）；结论通过校验以前因故障结束的才算没有结论，之后的故障不改变结论（第 8 条）

  验证：七处和 proposal 的说法一致；`git diff` 里完整版 Proposal 没有其他改动

## 2. contracts：结束原因、Critic 审核和 PM 回应的格式

- [ ] 2.1 先写测试 `tests/contracts/test_review.py`，每个场景一个测试：spec「Critic 审核和 PM 回应的格式」的七个场景。验证：运行这个测试文件，确认因为 `contracts/review.py` 不存在而失败
- [ ] 2.2 实现 `contracts/review.py`（design D9）和 `contracts/case.py` 的 `CaseEndReason`（design D8）。验证：2.1 的测试通过，`tests/contracts` 其余测试不受影响；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. 配置

- [ ] 3.1 先写测试 `tests/modules/scheduler/test_config.py`，增加 spec「案件阶段的配置」的「默认配置」「配置不合法」两个场景，并检查两个下限（0 和 1）合法。验证：运行这个测试文件，确认新测试因为没有这两个配置项而失败，已有的配置测试不受影响
- [ ] 3.2 `SchedulerConfig` 增加 `max_resubmissions`、`max_challenge_rounds`（design D10）。验证：3.1 的测试和已有的调度器测试通过；`uv run mypy` 通过

## 4. 案件的状态和结论

- [ ] 4.1 先写测试：
  - `tests/modules/scheduler/builders.py` 增加 spec 的基准数据（design D13）：开仓案件、退出案件，按事件序列把案件推到某个阶段的辅助函数
  - `tests/modules/scheduler/domain/test_stage.py`，每个场景一个测试：「案件的状态」两个场景；「案件的阶段」的「唤醒 PM 和复盘调用模型、没有方案」；「唤醒 PM 和结论」五个场景

  验证：运行这个测试文件，确认因为 `domain/stage.py` 不存在而失败
- [ ] 4.2 实现 `domain/stage.py`：`CaseStage`、`CriticOutcome`、`CaseState` 的字段和三个推出的属性、`new_case`、`reject_conclusion`、`accept_conclusion`、`_ignores`、`_end`（design D2、D3、D7）；从 `__init__.py` 导出（测试只从包根 import）。验证：4.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过（domain 只用了白名单里的标准库）

## 5. Critic 审核、PM 回应和 Critic 不可用

- [ ] 5.1 先写测试，写在 `test_stage.py`，每个场景一个测试：「Critic 审核开仓类方案」八个场景，「PM 回应」三个场景，「Critic 审核退出类方案」三个场景，「Critic 不可用」两个场景，「案件的阶段」的「PM 回应也在调用模型」，「案件阶段的配置」的「改配置改变转换」。验证：运行，确认这些测试因为没有 `review`、`respond`、`critic_unavailable` 而失败，第 4 组的测试仍然通过
- [ ] 5.2 实现 `review`、`respond`、`critic_unavailable`、`_to_risk`（design D4、D5）。验证：5.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 6. 风控、人工确认和等下单

- [ ] 6.1 先写测试，写在 `test_stage.py`，每个场景一个测试：「风控」四个场景，「人工确认」五个场景（确认消息的说明检查 Critic 审核的结果），「等下单」四个场景，「案件的阶段」的「开仓案件走完各阶段」。验证：运行，确认这些测试因为缺少对应的方法而失败
- [ ] 6.2 实现 `risk_checked`、`confirm`、`decline`、`confirmation_timed_out`、`order_submitted`、`recheck_voided`（design D6）。验证：6.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check` 通过

## 7. 作废、中止、系统故障、复盘和结束

- [ ] 7.1 先写测试，写在 `test_stage.py`，每个场景一个测试：「方案被作废和紧急退出中止」四个场景，「系统故障」三个场景，「复盘案件」两个场景，「结束原因」三个场景（包括要释放预留），「已结束的案件和不适用的事件」两个场景；另外逐个检查 13 种结束原因的"有结论"和 spec「结束原因」的表一致。验证：运行，确认这些测试因为缺少对应的方法而失败
- [ ] 7.2 实现 `void`、`abort`、`fail`、`finish_retrospective`（design D7）。验证：7.1 和之前的测试都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 8. 性质测试

- [ ] 8.1 写 `tests/modules/scheduler/domain/test_stage_machine.py`（design D13）：Hypothesis 的 `RuleBasedStateMachine` 随机取案件类型和两个配置项，每个事件方法一条规则，按 spec「已结束的案件和不适用的事件」的表检查不变、报错或执行，每一步以后检查 D13 列出的不变量。验证：
  - `HYPOTHESIS_PROFILE=ci uv run pytest tests/modules/scheduler` 通过
  - 用 `event` 确认样例里出现过：13 种结束原因、三种 Critic 审核的结果、重交用完、质询满轮数
  - 如果找到反例，先确认是实现的错误还是 spec 的漏洞：是 spec 的漏洞就先更新 spec 和 design，再改代码

## 9. 案件队列的场景

- [ ] 9.1 按 case-queue 的 delta spec 更新 `tests/modules/scheduler/domain/test_queue.py` 里对应的测试：「降级期间照常不再运行和作废」「已经确认的开仓方案也作废」「退出类方案不作废」「暂时启动不了的排队案件也算」「开仓类和退出类、各个阶段的方案都作废」「复盘案件不中止」，让等下单的方案都属于一个处在等下单阶段的运行中案件，断言按新的场景写。只改测试数据和断言，不改 `domain/queue.py`。验证：`uv run pytest tests/modules/scheduler` 通过

## 10. 整体检查

- [ ] 10.1 对照两份 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 10.2 按 CI 的顺序在本地完整跑一遍（数据库测试要开着 Docker）：`uv sync --locked`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`HYPOTHESIS_PROFILE=ci uv run pytest`、`npx --yes @fission-ai/openspec@1.13.2 validate --all --strict --no-interactive`（和 CI 固定的版本一致）。验证：全部通过；`git status` 里没有探针文件和缓存目录残留
