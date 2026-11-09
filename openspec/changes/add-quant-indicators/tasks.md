# Tasks

## 1. 结果和配置

- [x] 1.1 测试：`tests/modules/marketdata/test_config.py` 覆盖 quant-indicators spec「量化指标配置」的场景：ATR 根数为 0、均线长度为空、均线长度里有 0 时配置报错
- [x] 1.2 实现：`contracts/quant.py` 的结果模型（D1）；`modules/marketdata/config.py` 的 `IndicatorConfig` 和 `MarketDataConfig.indicators`；用 `uv run pytest tests/modules/marketdata` 验证

## 2. 指标的计算

- [x] 2.1 测试：`tests/modules/marketdata/domain/test_indicators.py` 覆盖 spec「现价」「均线」「相对强度」「ATR」「日线 ATR 和一年位置」「量比」「前高前低」「跳空缺口」「52 周高低点」「事件反应」里不涉及请求的场景，参数从 config 读：有和没有最新价；均线的三个数字、根数不够、不够算斜率；区间涨跌和相对强度、多取的不算、没有板块 ETF；ATR 的三个数字、根数不够；一年位置、不满一年；用请求里的量比、用 15 分钟线算、过去有提前收盘的一天、过去没有 15 分钟线；找出前高前低、一样高、每边只列最近的、范围以前的不算；补了一部分、补上了、向下、今天跳空、现价在缺口里；52 周高低点、不满一年；盘中、开盘以前、截止以后、正好在 K 线结束时的事件，没有板块 ETF，没有事件
- [x] 2.2 实现：`modules/marketdata/domain/indicators.py` 的 `IndicatorRequest`、`EventTime` 和各项计算，结果按 D11 只在最后舍入；用 `uv run pytest tests/modules/marketdata/domain` 验证

## 3. 取数和用例

- [x] 3.1 测试：`tests/modules/marketdata/domain/test_indicators.py` 加 spec「取数」里交易日数的场景：范围比均线要的长、60 分钟线遇到提前收盘、周线按周数、有事件和没有量比时 15 分钟线的交易日数；`tests/modules/marketdata/test_indicators_service.py` 用假 K 线源覆盖「量化指标的请求和结果」「取数」「数据源不可用」的场景：结果写明截止时刻和现价、股票没有 K 线、取数的请求、有事件没有量比时取 15 分钟线、没有板块 ETF 时只取股票和 SPY、日线不可用
- [x] 3.2 实现：domain 的交易日数（D4）；`service.py` 的 `IndicatorService`，包根导出 `IndicatorService`、`IndicatorRequest`、`EventTime`、`IndicatorConfig`；用 `uv run pytest tests/modules/marketdata` 验证
- [x] 3.3 运行 `uv run pytest`、`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`openspec validate add-quant-indicators --strict`

## 4. 文档和冒烟

- [x] 4.1 完整版 Proposal「Quant」的「代码算的数字」：波动写明日线 ATR 在过去一年里的位置；关键价位只列离现价最近的几个；事件反应也比 SPY；用 `rg` 确认和本变更一致
- [ ] 4.2 主机冒烟：提交部署以后，用部署的代码和生产 venv、真实 Alpaca，key 走 ssh stdin，不留东西：NVDA（板块 XLK）四种周期各算一次，带一个今天的事件、不带量比，看各项数字是否齐全、耗时；NVDA 回放到 2024-06-10 盘中，日线和缺口按拆股以后的口径
