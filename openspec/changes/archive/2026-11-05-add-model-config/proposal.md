# Proposal

## Why

各 agent 的模型现在写死在 `stand_in_models()` 里，预算额度是代码里的默认值。接真模型以前要先有配置文件：每个 agent 用什么模型、单价多少、每天多少预算都写在一处，以后每个 agent 才能单独从替身换成真模型。

## What Changes

- 新增模型文件 `src/tradingagentspro/bootstrap/models.toml`：六个 agent 的主模型和备用模型、模型的单价、三个预算的额度
- 启动时在「装配」这一步读模型文件，网关的模型配置和三个额度都来自它
- 文件的内容是现在的替身模型，单价 0，案件的行为不变
- 运行时的额度是普通预算 20、持仓保留预算 10、寻股预算 5 美元
- 文件不存在、不是合法的 TOML、内容不合要求时启动失败
- 删除 `stand_in_models()`，测试用的替身模型配置挪到 `tests/fakes/`

## Capabilities

### New Capabilities

- `model-config`：模型文件的位置、内容、读取和报错

### Modified Capabilities

- `bootstrap`：「模拟盘的装配」的 Agent 网关一行，「启动失败」加模型文件这一项
- `stand-ins`：「替身的模型」写在模型文件里

## Impact

- 代码：新增 `bootstrap/models.py`、`bootstrap/models.toml`；改 `bootstrap/app.py`、`integrations/standin`
- 数据：没有迁移
- 依赖：不加新依赖，用标准库的 tomllib
- 部署：不加环境变量，不改 `deploy/` 下的文件；换模型就是改文件、提交，再按「更新」的步骤拉取和重启

## 引用的 Proposal 章节

- 完整版「Agent」：每个 agent 用哪个模型写在配置文件里，备用模型，厂商校验
- 完整版「调度器与运行模块」：每日模型调用预算
- 完整版「技术栈与部署」的模型一行

## Non-goals

- API key 和按厂商分派到真模型：属于「模型适配器」
- 选定真模型：文件里现在都是替身，换哪个模型等接入各 agent 时定
- 缓存、分时段、分档的价格：单价只有输入和输出，缓存命中按 call-budget spec 算输入价，记下的费用可能偏高，额度放宽后足够覆盖
- 网关的其他阈值（超时、次数、熔断）：继续留在代码的默认值里
- 模型的调用参数（max_tokens、thinking、effort）：属于「模型适配器」和各 agent 的调用
- 记下实际的模型版本：模型名不一定锁定版本，属于「模型适配器」
- 运行中重新读文件：改文件要重启才生效

## 需要人决定

无
