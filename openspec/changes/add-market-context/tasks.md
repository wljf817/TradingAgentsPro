# Tasks

## 1. 合同和行情模块

- [x] 1.1 `contracts/snapshot.py` 加 `VixClose`、`ScheduleItem`、`ScheduleKind`，`MarketEntry` 加带默认值的 `vix`、`schedule`；写测试验证旧快照的 JSON 读回时这两项为空，运行 `uv run pytest tests/contracts` 通过
- [x] 1.2 先写 `market-context` spec「VIX 收盘值」「重要日程」的测试，再在 `modules/marketdata` 加 VIX 源端口、不可用错误、日程规则、配置和市场背景服务；运行 `uv run pytest tests/modules/marketdata` 通过
- [x] 1.3 配置里写入 2026-11 到 2027-12 的 FOMC 决议、CPI、非农日期，FOMC 取自美联储日历，CPI 和非农取自 BLS 日历；逐条核对来源，日期都是交易日的测试不写，运行 `uv run mypy` 通过

## 2. Cboe 数据源

- [x] 2.1 先写 `market-context` spec「Cboe 数据源」的测试，再加 `integrations/cboe`；运行 `uv run pytest tests/integrations/cboe` 通过
- [x] 2.2 加连真实 Cboe 的冒烟测试（标记 `news_sources`）；在服务器上运行通过

## 3. 快照和装配

- [x] 3.1 先写 `state-snapshot` spec「共享事实」新场景的测试，再改 `workflows/snapshot_flow.py`；运行 `uv run pytest tests/workflows` 通过
- [x] 3.2 `bootstrap/app.py` 装配 Cboe 数据源，快照配置的指数加上 VIXY，更新 bootstrap 的测试；运行 `uv run pytest tests/bootstrap` 通过
- [x] 3.3 完整版 Proposal「技术栈与部署」的数据源一行加上 Cboe；`grep Cboe` 能查到

## 4. 验证

- [x] 4.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check` 和 `uv run mypy`，全部通过
