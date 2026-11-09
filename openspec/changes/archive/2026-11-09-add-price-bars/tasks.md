# Tasks

## 1. Alpaca 的原始 K 线

- [x] 1.1 测试：`tests/integrations/alpaca/test_bars.py` 用替身服务器覆盖 alpaca-bars spec「原始 K 线」「行情模块的拆股记录」「原始 K 线的失败」的场景：15 分钟线和日线的请求参数（带 end、adjustment raw、不分批）和映射、分页、没有的股票不在结果里、行情模块取拆股记录、超时、429、503、403、缺字段、拆股记录 503；现有场景照常通过；`test_bars_smoke.py` 加真实 Alpaca 的一条：NFLX 2025-11-14 20:45Z 的原始 15 分钟线收盘价在 1,000 以上，2025-11-17 14:30Z 的在 200 以下
- [x] 1.2 实现：`modules/marketdata` 的 `SourceBar`、`SourceDayBar`、`Split`、`BarsUnavailableError` 和 `BarHistorySource` 端口（包根导出）；`integrations/alpaca/bars.py` 的 `AlpacaBarHistory`，和 `AlpacaBarSource` 共用请求、分页和拆股映射；用 `uv run pytest tests/integrations/alpaca` 验证

## 2. K 线的规则

- [x] 2.1 测试：`tests/modules/marketdata/domain/test_bars.py` 用 price-bars spec 的基准交易日历覆盖「K 线的范围」「15 分钟线」「60 分钟线」「日线」「周线」「拆股换算」里不涉及请求的场景：盘中、开盘正好 15 分钟和不到 15 分钟、休市日、周线从第一周算起；去掉盘前盘后、提前收盘、没走完的、范围以前的；拼成一根、最后一根半小时、提前收盘那天、没走完的一小时；收盘以后、盘中由 15 分钟线拼成（不用数据源当天的日线）、提前收盘那天；拼成一周、这一周没走完、提前收盘的周五；拆股以前、回放到拆股以前、除权日开盘不到半小时、反向拆股、舍入
- [x] 2.2 实现：`modules/marketdata/domain/bars.py` 的 `Bar`、范围、筛选、按组拼、日线和周线、拆股换算；用 `uv run pytest tests/modules/marketdata/domain` 验证

## 3. 取 K 线

- [x] 3.1 测试：`tests/modules/marketdata/test_bars_service.py` 用假 K 线源覆盖 price-bars spec「K 线的请求和结果」「截止时刻」「取数的请求」「数据源不可用」和「K 线配置」的场景：结果写明截止时刻、没有某只股票、按延迟算截止、延迟 30 分钟；15 分钟线、盘中日线、收盘以后日线、周线各自的请求和拆股记录的请求；K 线和拆股记录不可用；延迟为负时配置报错；延迟从 config 读
- [x] 3.2 实现：`config.py` 的 `bar_delay`；`BarService` 和 `Bars`，包根导出；`tests/fakes/` 加假 K 线源；用 `uv run pytest tests/modules/marketdata` 验证
- [x] 3.3 运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate add-price-bars --strict`

## 4. 文档和冒烟

- [x] 4.1 完整版 Proposal「Quant」的数据一段：截止时刻是快照时刻减 15 分钟，实盘和回放一样；拆股只算快照那天以前的；60 分钟线从开盘起由 15 分钟线拼，没走完的 K 线由走完的 15 分钟线拼；用 `rg` 确认和本变更一致
- [x] 4.2 主机冒烟：提交部署以后，用部署的代码和生产 venv、真实 Alpaca，key 走 ssh stdin，不留东西：NVDA、XLK、SPY 四种周期各取一次，看根数、耗时和最后一根是否标明没走完；NVDA 回放到 2024-06-07 收盘以后，日线是拆股以前的价格（1,000 美元以上）
