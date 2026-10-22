# Spec Delta

## MODIFIED Requirements

### Requirement: 密钥

密钥 SHALL 按下表存放，MUST NOT 出现在仓库、systemd 单元文件和进程的命令行里：

| 密钥 | 存放在哪里 | 谁能读 |
| --- | --- | --- |
| 数据库密码、Alpaca 的 key 和 secret、Discord token、Finnhub 的 API key | 部署机器上的环境变量文件 | 只有 root；systemd 读取后作为环境变量交给服务进程 |
| PostgreSQL 超级用户的密码 | 部署机器上 PostgreSQL 的环境变量文件 | 只有 root |
| moomoo 的登录密码 | 不写进任何文件；OpenD 第一次交互登录时记住 | OpenD（服务用户） |
| 克隆仓库用的 Deploy key | 服务用户的 SSH 目录 | 只有服务用户；在 GitHub 上只读 |

- 仓库里的环境变量模板 SHALL 只有变量名和说明，没有值
- 主程序的环境变量模板 SHALL 恰好列出主程序读取的全部环境变量（bootstrap spec「环境变量」的表），顺序相同
- 单元文件引用的每个环境变量文件，仓库里都 SHALL 有对应的模板
- moomoo 的登录账号不是密钥，放在 OpenD 的环境变量文件里，出现在 OpenD 的命令行上

#### Scenario: 模板和程序读取的变量一致

- **GIVEN** 主程序新增了一个环境变量
- **WHEN** 只改了程序，没有改主程序的环境变量模板
- **THEN** 测试失败，指出模板缺少的变量

#### Scenario: 模板里没有值

- **WHEN** 检查仓库里的三个环境变量模板
- **THEN** 每个变量的等号后面都是空的

#### Scenario: 别的用户读不到密钥

- **GIVEN** 部署完成
- **WHEN** 服务用户或者其他非 root 用户读取环境变量文件
- **THEN** 没有权限

#### Scenario: Deploy key 不能推送

- **WHEN** 在部署机器上用 Deploy key 向仓库推送
- **THEN** GitHub 拒绝推送
