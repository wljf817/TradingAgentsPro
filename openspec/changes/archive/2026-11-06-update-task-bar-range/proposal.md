# Proposal

## Why

Proposal 要求 Quant 的任务写明周期和范围，并且周期或范围不同就是不同的任务，但任务请求和 Task 里都没有这两项。不补上，Quant 不知道该取什么 K 线，同一只股票日线和周线的两个任务还会被当成相同任务复用。

## What Changes

- 任务请求和 Task 加周期和范围：周期是周线、日线、60 分钟线、15 分钟线之一，范围是往前多少个交易日
- Quant 的任务必须写这两项，别的任务不能写，否则请求格式错误
- 周期或范围不同就是不同的任务，不复用
- 范围超过这个周期的上限时，请求被拒绝，原因是"范围超过上限"。上限是配置项：周线 1260、日线 504、60 分钟线 20、15 分钟线 5 个交易日
- PM 替身派给 Quant 的任务写日线、60 个交易日

## Capabilities

### New Capabilities

无

### Modified Capabilities

- `task-dispatch`：任务请求和 Task 的格式、相同的任务、派发的顺序、派发的配置
- `stand-ins`：PM 替身派给 Quant 的任务写周期和范围

## Impact

- 引用完整版 Proposal：「Quant」的周期和范围表，「Task / Result 通信」的规则和任务清单
- `contracts/task.py`、`modules/gateway/domain/dispatch.py`、`modules/gateway/config.py`、`integrations/standin/conclusions.py`
- Task 整份存在 JSONB 里，不需要迁移；已经存着的 Task 读回时周期和范围为空
- Non-goals：不取 K 线、不算指标、不接 Quant 的模型调用；这些在 Quant 的变更里做

## 需要人决定

无。Proposal 的范围写的是 5 年和 2 年，这里统一成交易日数，默认 1260 和 504，见 design D2。
