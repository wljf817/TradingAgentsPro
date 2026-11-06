# Proposal

## Why

风控、网关、调度等阈值现在只是代码里的默认值，要改就得改代码再部署；数据库地址和 Alpaca、Discord 等密钥在 root 才能读的环境变量文件里，模型的地址和 key 却已经在 `config/models.toml`。接真模型以后要常调这些值，所以把主程序的参数和密钥都收进 `config/`，一个部署一份，改完重启生效。

## What Changes

- 新增配置文件 `config/settings.toml`：不进 git，不存在时启动自动生成，权限 0600。主程序的连接信息、密钥和全部运行参数都在这里，每个配置类一节
- 生成的文件只有九个必填项是空的，其余的项写成注释，注释里是代码当前的默认值；去掉注释才生效，默认值继续留在代码里，文件从代码生成
- **BREAKING** 主程序不再读环境变量，`deploy/tradingagentspro.env.example` 和主程序 systemd 单元的 `EnvironmentFile` 去掉。第一次启动时必填项是空的，一次列出全部缺少的项，启动失败
- 启动的第 1 步改成读配置文件和模型文件：两个文件都先生成再读，任何配置错误都在连接数据库以前报出来
- 启动时把和默认值不同的项写进日志
- 写在别处的值挪进配置：SEC 和 Cboe 的地址；快照的指数默认值改成线上用的 SPY、QQQ、VIXY；交易日历的范围从启动配置挪到 `calendar` 一节
- 参数清单写进 design：哪些挪进来，哪些留在代码里、为什么
- 线上的数值全部不变

## Capabilities

### New Capabilities

- `settings-file`：配置文件的位置、内容、生成、出错和日志

### Modified Capabilities

- `bootstrap`：去掉「环境变量」，加「读配置文件」；「命令行」「日志」「启动顺序」「启动失败」「数据库」「模拟盘的装配」跟着改
- `deployment`：「密钥」「全部重置」「部署的配置项」
- `model-config`：「读到以后怎么用」：网关的其他项来自配置文件
- `discord-notification`：「Discord 配置」改从配置文件读
- `finnhub-company-data`：「Finnhub 配置」
- `sec-filings`：「SEC 配置」加两个地址
- `market-context`：「Cboe 数据源」的地址可以配置
- `state-snapshot`：「快照配置」的指数默认值
- `manual-plans`：「提交接口」的配置说明
- `program-log`：「系统的动作」里读配置失败的说法

## Impact

- 代码：`bootstrap/settings.py` 重写，新增 `bootstrap/settings_text.py`、`bootstrap/files.py`；改 `bootstrap/app.py`、`bootstrap/cli.py`、`bootstrap/models.py`、`integrations/nyse_calendar.py`、`integrations/sec`、`integrations/cboe`、`integrations/discord/config.py`、`workflows/snapshot_flow.py`
- 测试：`tests/bootstrap`、`tests/deploy`、`tests/integrations/discord`
- 数据：没有迁移
- 依赖：没有新增
- 部署：主机要手工迁移一次，步骤写在 `deploy/README.zh.md` 和 `deploy/README.md`；`postgres.env`、`opend.env` 留在 `/etc/tradingagentspro/`
- 文档：两份部署说明、`README.zh.md`、两份路线图、完整版 Proposal 的一句话

## 引用的 Proposal 章节

- 完整版「技术栈与部署」：可靠性要求那句改成密钥存在不进 git 的配置文件

## Non-goals

- 改任何阈值的数值：这个变更只挪位置
- 运行中重新读文件：改完重启生效
- 配置项之间新增校验：沿用各配置类已有的校验
- 把 `postgres.env`、`opend.env` 收进来：它们是 Docker 和 systemd 读的，理由见 design D9
- systemd、compose、备份脚本里的值：deployment spec「部署的配置项」已经规定各在一处
- 在主机上迁移和部署：由人按部署说明做
- 模型文件的格式：不变
- 冒烟测试的环境变量：测试继续从 `dev.env` 读，它不是主程序

## 需要人决定

无
