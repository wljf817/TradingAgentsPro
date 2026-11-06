# Design

## Context

`TaskRequest` 和 `Task` 现在只有任务类型、股票、问题和输入。`same_task` 比较这四项，所以两个只有周期不同的 Quant 任务会被复用。网关的检查在 `dispatch_rejection`，配置在 `GatewayConfig`。

## Goals / Non-Goals

**Goals：** Quant 的任务带上周期和范围，派发和复用都认这两项。

**Non-Goals：** 取 K 线、算指标和调用 Quant 的模型。

## 模块边界

只涉及 Agent 网关。`contracts/task.py` 的 `TaskRequest`、`Task` 加 `bars`，新增 `BarPeriod`、`BarWindow`；`modules/gateway` 按它们检查和比较；替身只是写这两项。模块之间传的模型不变。

## Decisions

### D1 周期和范围是任务请求的两项

`TaskRequest` 加 `bars: BarWindow | None = None`，`BarWindow` 有周期 `period` 和交易日数 `days`，缺一项就是格式错误。写不写由 `TaskRequest` 的 validator 按 `task_type.agent` 检查：Quant 必须写，别的不能写。这是格式错误，和缺少问题一样。周期和范围总是一起出现，放在一个模型里，比较和检查都不用分别判空。

**不放进问题文字：** 问题是给模型看的补充，网关要比较和检查上限，必须是结构化的。

### D2 范围统一用交易日数

四种周期都用往前多少个交易日。Proposal 的 5 年和 2 年换成 1260 和 504 个交易日，按一年 252 个交易日算。一种单位只有一种写法，模型不用换算，检查也只有一个比较。

### D3 上限是配置，超过时拒绝

上限依赖配置，不能放进 contracts 的 validator。`GatewayConfig` 加四个正整数配置项，`dispatch_rejection` 检查，排在股票和寻股之后、轮次之前：被拒绝的请求不占轮次和任务数。原因是新的 `TaskRejection`。

四个配置项写成平铺的字段，不用字典，配置文件里每项一行，和 `GatewayConfig` 其他项一样。

### D4 Task 不校验这两项

`Task` 的 `bars` 默认 `None`，没有 validator。已经存着的 Quant 任务没有这两项，校验会让它们读不出来；`Task` 由 `new_task` 从通过校验的请求照抄，不会出错。

### D5 替身写固定值

`integrations/standin/conclusions.py` 的 Quant 请求写日线、60 个交易日。替身只要过格式，写成模块里的常量。

## 省略的情况

- 已经存着的任务不补这两项

## Risks / Trade-offs

- 模型可能写出超过上限的范围：被拒绝的 Result 交回发起者，它可以改了再派
