# Spec Delta

## MODIFIED Requirements

### Requirement: 密钥

密钥 SHALL 按下表存放，MUST NOT 出现在仓库、systemd 单元文件和进程的命令行里：

| 密钥 | 存放在哪里 | 谁能读 |
| --- | --- | --- |
| 数据库密码、Alpaca 的 key 和 secret、Discord token、Finnhub 的 API key | 主程序工作目录下的配置文件 `config/settings.toml`（settings-file spec），不进 git，不在备份里 | 服务用户 |
| 模型厂商的 API key | 主程序工作目录下的模型文件 `config/models.toml`（model-config spec「模型文件」），不进 git，不在备份里 | 服务用户 |
| PostgreSQL 超级用户的密码 | 部署机器上 PostgreSQL 的环境变量文件 | 只有 root |
| moomoo 的登录密码 | 不写进任何文件；OpenD 第一次交互登录时记住 | OpenD（服务用户） |
| 克隆仓库用的 Deploy key | 服务用户的 SSH 目录 | 只有服务用户；在 GitHub 上只读 |

- 仓库里的环境变量模板 SHALL 只有变量名和说明，没有值
- 主程序 MUST NOT 用环境变量文件：主程序的 systemd 单元不引用环境变量文件
- 单元文件引用的每个环境变量文件，仓库里都 SHALL 有对应的模板
- moomoo 的登录账号不是密钥，放在 OpenD 的环境变量文件里，出现在 OpenD 的命令行上

#### Scenario: 模板和程序读取的变量一致

- **GIVEN** OpenD 的单元在命令行上用了一个新的环境变量
- **WHEN** 只改了单元，没有改 OpenD 的环境变量模板
- **THEN** 测试失败，指出模板缺少的变量

#### Scenario: 模板里没有值

- **WHEN** 检查仓库里的两个环境变量模板
- **THEN** 每个变量的等号后面都是空的

#### Scenario: 别的用户读不到密钥

- **GIVEN** 部署完成
- **WHEN** 服务用户或者其他非 root 用户读取环境变量文件
- **THEN** 没有权限

#### Scenario: 别的用户读不到模型文件

- **GIVEN** 主程序生成了模型文件
- **WHEN** 服务用户以外的非 root 用户读取它
- **THEN** 没有权限

#### Scenario: 别的用户读不到配置文件

- **GIVEN** 主程序生成了配置文件
- **WHEN** 服务用户以外的非 root 用户读取它
- **THEN** 没有权限

#### Scenario: Deploy key 不能推送

- **WHEN** 在部署机器上用 Deploy key 向仓库推送
- **THEN** GitHub 拒绝推送

### Requirement: 全部重置

要从头开始时，人 SHALL 同时重置状态库和 Alpaca 模拟盘账户：

1. 停止主程序
2. 删掉并重建数据库，`public` 和 `dbos` 两个 schema 一起清空
3. 在 Alpaca 后台重置模拟盘账户，初始资金设为「模拟盘初始资金」，生成新的 key，换进配置文件
4. 启动主程序

- MUST NOT 只清空状态库而不重置模拟盘账户，也 MUST NOT 只重置模拟盘账户而不清空状态库

#### Scenario: 全部重置

- **WHEN** 人按步骤全部重置
- **THEN** 主程序启动完成，启动对账没有不一致
- **AND** 模拟盘账户的权益是 2,000 美元，状态库里没有任何记录

#### Scenario: 只清空状态库

- **GIVEN** 模拟盘账户里有以前挂过的止损单，比如 `AAPL-STOP-1`
- **WHEN** 只清空状态库、不重置模拟盘账户，之后又要给 AAPL 挂止损单
- **THEN** 新的止损单用同一个客户端订单 ID，被券商拒绝；所以部署说明要求两者一起重置

#### Scenario: 只重置模拟盘账户

- **GIVEN** 状态库里记着持仓和挂着的止损单
- **WHEN** 只重置模拟盘账户、不清空状态库，然后启动主程序
- **THEN** 启动对账写出不一致，执行模块暂停开仓；所以部署说明要求两者一起重置

### Requirement: 部署的配置项

部署 SHALL 提供下表的配置项，每项只在一个地方设定：

| 配置项 | 默认值 | 在哪里设定 |
| --- | --- | --- |
| 主程序重启间隔 | 30 秒 | 主程序的 systemd 单元 |
| OpenD 重启间隔 | 30 秒 | OpenD 的 systemd 单元 |
| 停止等待上限 | 90 秒 | 主程序的 systemd 单元 |
| 等状态库就绪的上限 | 300 秒 | 状态库的 systemd 单元 |
| 备份时刻 | 每天 06:00 UTC | 备份的 systemd 定时器 |
| 备份保留份数 | 14 | 备份脚本 |
| 模拟盘初始资金 | 2,000 美元 | Alpaca 后台（部署说明） |

- 主程序重启间隔 MUST NOT 短于 30 秒：主程序每次启动都会向 Discord 重新注册 `/plan`
- 主程序自己的参数 SHALL 写在配置文件里（settings-file spec），上表只有主程序以外的进程读的值

#### Scenario: 改保留份数

- **GIVEN** 备份脚本里的保留份数改为 7
- **WHEN** 备份目录里已经有 7 个备份文件，备份成功
- **THEN** 留下最新的 7 个

#### Scenario: 默认值

- **WHEN** 按仓库里的部署文件部署
- **THEN** 各配置项都是上表的默认值
