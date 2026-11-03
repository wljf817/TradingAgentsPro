# Tasks

## 1. 前提

- [x] 1.1 按 proposal「需要人决定」1 修改完整版 Proposal「技术栈与部署」的定时心跳那一句（propose 时已改）。验证：`grep -n "每个交易日的定时心跳是一个持久化工作流" "docs/AI Agent 美股交易系统需求 Proposal.md"` 找到改过的一处；简版不改
- [x] 1.2 本变更的 bootstrap delta 的「模拟盘的装配」是在 add-thesis-conditions-and-price-alerts 改过的版本上改的；本变更先归档，所以把那个变更 delta 里的同一节同步成本变更的版本（多出收盘心跳一行和「收盘心跳排进的案件跑起来」一个场景），不论哪个先归档，这一节都不丢。验证：两个 delta 的这一节 `diff` 没有差异

## 2. 到期的判断

- [x] 2.1 先写测试 `tests/modules/heartbeat/domain/test_heartbeat_day.py`（design D1）：close-heartbeat spec「什么时候做」里只看时刻的场景（收盘加延迟以后到期、盘中不做、周末做周五的、下一个交易日开盘以后不补做、半日市）和「收盘心跳的配置」的改延迟（延迟 0、30 分钟）；纯函数的延迟是参数，测试按场景传 15 分钟、0、30 分钟，默认值由第 5 组的线程测试从配置读。验证：确认因为 `heartbeat_day` 不存在而失败
- [x] 2.2 实现 `modules/heartbeat/domain/close_check.py` 的 `heartbeat_day(calendar, now, delay) -> date | None`，`__init__.py` 导出，模块说明加上到期的判断。验证：2.1 的测试和原有的 `tests/modules/heartbeat` 通过；`uv run pytest tests/architecture`、`uv run mypy`、`uv run lint-imports` 通过

## 3. 调度器和程序日志

- [x] 3.1 `modules/scheduler/domain/case_type.py` 的 `_HEARTBEAT_TYPES` 改为公开的 `HEARTBEAT_CASE_TYPES`，`modules/scheduler/__init__.py` 导出；`modules/programlog/domain/entry.py` 加 `Action.CLOSE_CHECK = "close_check"`（design D3、D4）。不加测试：只是公开一个已有的集合和加一个动作的值。验证：原有的 `tests/modules/scheduler`、`tests/modules/programlog` 通过；`uv run mypy` 通过

## 4. 收盘心跳的工作流

- [x] 4.1 先写测试 `tests/workflows/test_close_heartbeat.py`（design D2、D3、D4）：照 `tests/workflows/test_case_workflow.py` 起一个 DBOS，install 拨钟的 `wait`；股票池、案件记录、公司数据源、队列用 `tests/fakes/` 的内存实现（没有的补上），运行中的案件用替身函数，intake 记下排队；覆盖 close-heartbeat spec「一次收盘心跳」「移出过期的候选」「刷新财报」「收盘检查的快照」「排进队列」（按开的顺序排进）「收盘检查的日志」「同一天只做一次」的全部场景，「收盘心跳的配置」的改刷新间隔，program-log spec 改过的「调度器的动作」的收盘检查、没有要开的检查项两个场景。中断用工作流在某一步以后抛出、再按同一个工作流 ID 恢复来模拟。验证：确认因为 `workflows/close_heartbeat.py` 不存在而失败
- [x] 4.2 实现 `workflows/close_heartbeat.py` 的 `HeartbeatParts`、`HeartbeatIntake`、`install`、`close_heartbeat` 工作流和它的四种 step、收盘检查条目的说明；工作流出错写 ERROR 再抛出；模块说明写明一天一个工作流、为什么这样分 step。验证：4.1 的测试和原有的 `tests/workflows` 通过；`uv run mypy`、`uv run lint-imports`、`uv run ruff check` 通过

## 5. 判断的线程

- [x] 5.1 先写测试（同一个测试文件，design D1、D5）：close-heartbeat spec「什么时候做」的已经开始过的不再做、收盘以后启动照样做、每隔间隔判断一次、降级时照常做、判断出错、停止；「收盘心跳的配置」的默认配置和配置非法。判断用记下 `started`、`start` 的 `HeartbeatRuns` 替身，另有一个测试用 `DbosHeartbeatRuns` 验证同一天的工作流 ID 只开始一次。验证：确认因为 `CloseHeartbeat`、`CloseHeartbeatConfig` 不存在而失败
- [x] 5.2 实现 `CloseHeartbeatConfig`、`HeartbeatRuns`、`DbosHeartbeatRuns`、`CloseHeartbeat`（start、stop、判断一次、守护线程 `close-heartbeat`，开始时写 INFO「收盘心跳已开始」和间隔）。验证：5.1 的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 6. 装配

- [x] 6.1 先写测试 `tests/bootstrap/`：bootstrap spec 改过的「模拟盘的装配」的收盘心跳排进的案件跑起来，「启动顺序」的正常启动的顺序，「DBOS」的启动和停止 DBOS、启动时接着做没做完的收盘心跳，「停止」的 SIGTERM、停止时巡检正在跟进订单；`tests/bootstrap/externals.py` 加收盘心跳用的拨钟 `wait`。验证：确认失败
- [x] 6.2 实现 `bootstrap/app.py`（design D6）：装配 `HeartbeatParts`，DBOS launch 以前 `close_heartbeat.install`，事件检测之后开始、盘中巡检之后关闭收盘心跳，写已关闭收盘心跳。验证：6.1 的测试和原有的 `tests/bootstrap` 测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 7. 收尾

- [x] 7.1 对照本变更的全部 spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 7.2 运行全部检查：`uv run pytest`（要开着 OrbStack）、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate add-close-heartbeat --strict`。验证：全部通过
