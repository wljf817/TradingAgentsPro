# Tasks

## 1. 前提

- [x] 1.1 按 proposal「需要人决定」1 修改完整版 Proposal「技术栈与部署」里自定义跟进的那一句（propose 时已改）。验证：`grep -n "自定义跟进由调度器每一轮检查到点以后排进队列" "docs/AI Agent 美股交易系统需求 Proposal.md"` 找到改过的一处；简版不改
- [x] 1.2 本变更的 bootstrap delta 的「模拟盘的装配」是在 add-thesis-conditions-and-price-alerts 改过的版本上改的（带收盘心跳和到价提醒），只多排队运行一行和一个新场景。那个变更要先归档：归档本变更以前确认它已经归档、`openspec/specs/bootstrap/spec.md` 的这一节和本变更 delta 只差这两处；它还没归档的，把这两处同步进它的 delta 再归档本变更。验证：apply 开始时 `diff` 两个 delta 只差这两处

## 2. 跟进的触发带跟进 ID

- [x] 2.1 先写测试：`tests/modules/scheduler/domain/` 里 case-types spec「触发」的场景「事件和卖出成交带的内容」加上跟进带跟进 ID 和问题；`tests/modules/scheduler/test_repo.py` 的跟进触发存下读回带同一个跟进 ID（queue-runner spec「排队的案件的存储」）；`tests/workflows/test_case_entries.py` 跟进的触发原因带跟进 ID（design D3）。验证：确认因为 `FollowUpTrigger` 没有 `follow_up_id` 而失败
- [x] 2.2 实现 `modules/scheduler/domain/case_type.py` 的 `FollowUpTrigger.follow_up_id`（放在最后）和 `workflows/case_entries.py` 的触发原因；改现有用到 `FollowUpTrigger(` 的测试和构造器。验证：2.1 的测试和 `tests/modules/scheduler`、`tests/workflows/test_case_entries.py` 通过；`uv run mypy` 通过

## 3. 跟进审批

- [x] 3.1 先写测试 `tests/modules/followups/test_service.py`（design D2、D4）：follow-ups spec 改过的「到点触发」全部场景（到点的边界、错过以后一起给出、触发时间相同按获批的先后、先排进再记为已触发、排进出错、关注时申请、买入以后照常触发），排进用记下调用、交回案件 ID、可以出错一次的替身；「读取一个跟进」两个场景；`test_service_threads.py` 的「审批和到点触发同时进行」改成排进；program-log spec「调度器的动作」的触发跟进（输出的引用是排进的案件 ID）、没有动作时不写（加读取一个跟进）；`test_repo.py` 加按跟进 ID 读出最新版本、读不存在的为 None。验证：确认因为 `trigger_due` 不收 `enqueue`、`follow_up` 不存在而失败
- [x] 3.2 实现 `FollowUpStore.get`、`PostgresFollowUpStore.get`、`tests/fakes/` 的内存实现 `get`，`FollowUpService.trigger_due(enqueue)` 和 `follow_up(follow_up_id)`，改服务和模块的说明。验证：3.1 的测试和 `tests/modules/followups` 全部通过（DB 测试要开着 OrbStack）；`uv run mypy`、`uv run lint-imports` 通过

## 4. 案件上下文

- [x] 4.1 先写测试：`tests/contracts/test_agent_call.py` 的 `FollowUpOrigin` 和 `CaseContext.follow_up`；`tests/workflows/test_case_flow.py` 的 case-workflow spec 改过的「案件上下文」的寻股和跟进案件的上下文、读不到跟进，以及其他场景里非跟进案件的跟进为空；agent-calls spec 改过的「每个 agent 的输入和输出」的跟进案件的案件上下文、持仓的案件上下文没有跟进（`tests/modules/gateway/test_agent_calls.py`）。验证：确认因为 `FollowUpOrigin` 不存在而失败
- [x] 4.2 实现 `contracts/agent_call.py` 的 `FollowUpOrigin` 和 `CaseContext.follow_up`（没有默认值），`workflows/case_flow.py` 的 `context`（design D5）；改 `tests/integrations/standin/snapshots.py` 等组 `CaseContext` 的地方。验证：4.1 的测试、`tests/contracts`、`tests/workflows`、`tests/modules/gateway`、`tests/integrations/standin` 通过；`uv run mypy` 通过

## 5. 排队运行

- [x] 5.1 先写测试 `tests/workflows/test_queue_runner.py`（design D1）：queue-runner spec 改过的「一轮」的到点的跟进同一轮启动、降级时到点的跟进照常排进、排进到点的跟进出错，以及原有场景照旧；program-log spec「调度器的动作」的一轮排进到点的跟进（排队、触发跟进、开案件的顺序）。跟进审批用真的服务加内存存储。验证：确认因为 `QueueParts` 没有 `follow_ups` 而失败
- [x] 5.2 实现 `QueueParts.follow_ups`、`run_round` 开头的到点触发和 `_enqueue_follow_up`，改模块说明里一轮的步骤。验证：5.1 的测试和 `tests/workflows` 全部通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 6. 装配

- [x] 6.1 先写测试 `tests/bootstrap/test_app.py`：bootstrap spec 改过的「模拟盘的装配」的到点的跟进开出的案件跑起来。验证：确认因为排队运行没有接上跟进审批而失败
- [x] 6.2 实现 `bootstrap/app.py`（design D6）。验证：6.1 的测试和 `tests/bootstrap` 通过；`uv run mypy`、`uv run lint-imports` 通过

## 7. 收尾

- [x] 7.1 对照本变更的全部 spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 7.2 运行全部检查：`uv run pytest`（要开着 OrbStack）、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate update-follow-ups-due --strict`。验证：全部通过
