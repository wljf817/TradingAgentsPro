# Design

## Context

- `ModelAssignments` 和 `AgentModels` 已经有全部校验：缺 agent、重复模型、Critic 和 PM 同厂商、缺单价。`ModelRef` 校验厂商和模型名非空。这个变更只把文件读进来，不重写校验。
- 三个额度是 `GatewayConfig` 的字段，默认值 2 / 1 / 0.5 是 call-budget 和 queue-runner 场景的基准。
- `start` 的每一步都包在 `step(...)` 里，任何异常都变成 `StartupError`，写日志、关闭已打开的部分、退出码 1。读文件的错误不用单独处理。
- 已验证：`uv build --wheel` 会把 `src/tradingagentspro/bootstrap/models.toml` 打进包里；`tomllib.loads(..., parse_float=Decimal)` 把 `20.00` 读成 `Decimal("20.00")`。

## 模块边界

- 只有 bootstrap 读模型文件。它用 gateway 模块公开的 `ModelAssignments`、`AgentModels`、`ModelPrice`、`GatewayConfig` 和 contracts 的 `ModelRef`、`Agent` 构造配置，再按原来的方式交给网关的各个服务。
- gateway 模块和 workflows 不改。`integrations/standin` 去掉 `stand_in_models()`，`StandInBackend` 不改。

## Goals / Non-Goals

**Goals:**

- 模型、单价和额度只在模型文件里设定，部署后的行为和现在一样，只有额度变大
- 文件写错时启动就失败，不带病运行

**Non-Goals:**

- 按厂商选后端：这个变更里后端仍只有替身
- 校验模型名真实存在：那要调用厂商，属于「模型适配器」

## Decisions

### D1 文件放在包里，进 git

路径 `src/tradingagentspro/bootstrap/models.toml`。模型和单价不是密钥，进 git 有历史，能跟代码一起回退；走现有的「更新」步骤，部署文件和环境变量都不用改。

- 放 `/etc/tradingagentspro/`：改要 root，没有历史，回退代码时不一起回退
- 放 `deploy/`：deployment spec 规定改了 `deploy/` 下的文件要重新安装 systemd 单元，换模型不需要这样
- 用环境变量指路径：只有一个文件，不需要

### D2 用 TOML，单价读成 Decimal

标准库 `tomllib` 就能读，不加依赖；`parse_float=Decimal` 让小数不经过浮点。YAML 的 pyyaml 现在只在开发依赖里，还要另外处理浮点。

### D3 模型写成"厂商/模型名"

和 spec、日志里的写法相同，文件也最短。用 `partition("/")` 在第一个 `/` 分开，缺的部分留空，由 `ModelRef` 报错，不另写判断。

### D4 严格读取，校验不重复

`bootstrap/models.py` 里用 `extra="forbid"` 的 Pydantic 模型接住文件结构，写错的项直接报错：`budgets` 三项必填，`agents` 的键是 `Agent`，`prices` 每项 `input`、`output` 必填。范围和业务校验留给 `GatewayConfig`、`ModelAssignments`。

`load_models(path)` 返回 `(ModelAssignments, GatewayConfig)`，`GatewayConfig` 只传三个额度。

### D5 `GatewayConfig` 的默认额度不动

默认值是 spec 场景的基准，20 个测试文件里 47 处 `GatewayConfig(...)` 靠它。运行时由 bootstrap 用文件里的值覆盖，和 `SnapshotConfig(indexes=...)` 一样。

- 去掉默认值：要改全部这些测试，行为没有差别
- 把额度搬进 `ModelAssignments`：要改 `domain/budget.py` 和 `queue_runner.py`，同样没有行为差别

### D6 在「装配」这一步最先读

`BootstrapConfig` 加 `models_file`，默认是 `Path(__file__).with_name("models.toml")`，测试可以指向写坏的文件。`start` 在 `step("装配")` 里先读，再把结果传给 `_assemble` 和 `_case_side`。先读文件，出错时 OpenD 还没有连。

### D7 测试的替身模型挪到 `tests/fakes/models.py`

函数原样挪过去。工作流和替身的测试不读仓库里的模型文件，否则以后把某个 agent 换成真模型，会牵连所有依赖替身的测试。bootstrap 的全程序测试走真实的装配，读仓库里的文件，但只依赖它能读进来。「仓库里的模型文件」也只测这一点，不测内容。

### D8 额度 20 / 10 / 5

和备用模型、单价一起写在模型文件里。现在全是替身，费用是 0，这几个数要到接真模型才起作用。

### 省略的情况

- 文件里写了真模型时，这个变更里仍由替身回答：分派属于「模型适配器」，读取时不拦
- 单价按整个请求一个价：Haiku 5.5 超过 100K token 的分档价、DeepSeek 分时段的价格都不表达
- 模型文件的编码错误、没有读权限：和文件不存在一样，抛异常后启动失败

## Risks / Trade-offs

- [记下的费用比账单高，额度提前用完] → 接真模型以后用 `gateway_costs` 和厂商账单对比，再调文件里的额度或加缓存单价
- [把写坏的文件部署到主机] → 第 4 步就失败，这时 Discord 还没有注册 `/plan`；按 deployment spec「更新」回到上一个提交
- [仓库文件只在本地测试里校验，CI 不跑 pytest] → 提交前跑本地测试，主机启动失败时按上一条回退

## Migration Plan

没有迁移。归档提交以后按「更新」部署，确认主机启动完成，日志里没有配置错误。回退就是回到上一个提交。
