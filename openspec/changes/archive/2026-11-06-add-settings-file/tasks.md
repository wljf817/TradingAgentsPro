# Tasks

## 1. 写在别处的值挪进配置类

- [x] 1.1 先写 `CalendarConfig` 的非法配置和默认范围的测试，再在 `integrations/nyse_calendar.py` 加 `CalendarConfig`，去掉 `DEFAULT_FIRST_DAY`、`DEFAULT_LOOKAHEAD`，`BootstrapConfig` 的两项挪走；运行 `uv run pytest tests/integrations/test_nyse_calendar.py` 通过
- [x] 1.2 先写「SEC 配置非法」新的一项和用配置地址请求的测试，再给 `SecConfig` 加 `base_url`、`data_url`，给 `CboeConfig` 加 `url`，适配器改用配置；运行 `uv run pytest tests/integrations/sec tests/integrations/cboe` 通过
- [x] 1.3 `SnapshotConfig.indexes` 的默认值改成 SPY、QQQ、VIXY，「改配置」的测试跟上 spec，其他场景的测试把指数配置为 SPY、QQQ；运行 `uv run pytest tests/workflows/test_snapshot_flow.py` 通过

## 2. 读配置文件

- [x] 2.1 先写 settings-file spec「文件的内容」「文件出错」的测试（`tests/bootstrap/test_settings.py`，加 `tests/bootstrap/settings_files.py` 的基准文件），再重写 `bootstrap/settings.py`：`DatabaseConfig`、`Settings`、`ELSEWHERE`、`load_settings(path)`；运行 `uv run pytest tests/bootstrap/test_settings.py` 通过
- [x] 2.2 先写「生成的文件」「第一次启动时生成」「已有的文件不改动」「文件不存在又生成不了」「不进 git」的测试，再加 `bootstrap/files.py` 的 `ensure_private_file` 和 `bootstrap/settings_text.py`：生成文本和 `ensure_settings_file`，`ensure_models_file` 改用 `ensure_private_file`；运行 `uv run pytest tests/bootstrap/test_settings.py tests/bootstrap/test_models.py` 通过
- [x] 2.3 先写「和默认值不同的项写进日志」的测试，再加比较的函数；运行 `uv run pytest tests/bootstrap/test_settings.py` 通过

## 3. 启动和装配

- [x] 3.1 先改 `tests/bootstrap/externals.py` 和 `tests/bootstrap/test_models.py`：`environment` 换成写配置文件的函数，全程序测试都用自己的 `settings_file` 和模板的 `models_file`，没有一个测试用默认路径；`load_models(path, gateway)` 的「其他项来自配置文件」先写测试再实现；运行 `uv run pytest tests/bootstrap/test_models.py` 通过
- [x] 3.2 先写 bootstrap spec「读配置文件」「启动失败」的新场景，再改 `start`、`run`、`cli.main`：第 1 步生成两个文件、读两个文件、写日志，`_assemble` 和 `_case_side` 全部用 `settings` 里的配置，`paper_execution` 打开错开；运行 `uv run pytest tests/bootstrap` 通过，`git status --ignored config` 里只有原来的 `config/models.toml`
- [x] 3.3 去掉 `DiscordConfig.from_env` 和变量名常量，`tests/integrations/discord` 改成直接构造配置，冒烟测试继续读环境变量；`integrations` 里提到环境变量的注释改成配置文件；运行 `uv run pytest tests/integrations/discord` 通过，`grep -rn 'environ\|getenv' src --include='*.py'` 没有结果

## 4. 部署文件和文档

- [x] 4.1 删 `deploy/tradingagentspro.env.example`，主程序单元去掉 `EnvironmentFile`，`tests/deploy` 跟上 deployment spec「密钥」：两个模板、主程序不用环境变量文件、OpenD 的模板和单元一致；运行 `uv run pytest tests/deploy` 通过
- [x] 4.2 改 `deploy/README.zh.md` 和 `deploy/README.md`：路径表、第一次部署、全部重置、更新、配置文件怎样改、不在备份里，加「迁移到配置文件」，写明九个变量和项的对照；`grep settings.toml deploy/README*` 两份都能查到，`grep -c 'tradingagentspro.env' deploy/README.zh.md` 只剩迁移一节的
- [x] 4.3 改 `README.zh.md` 的启动说明和完整版 Proposal「技术栈与部署」的可靠性要求；`grep` 确认没有「存环境变量」，简版 Proposal 没有改动
- [x] 4.4 两份路线图 M5「接入」组加「配置文件」一行，状态进行中，完成数改成 2 / 10；`grep` 两份都能查到

## 5. 验证

- [x] 5.1 运行 `uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports` 和 `openspec validate add-settings-file --strict`，全部通过；`uv build --wheel` 打出的包能生成配置文件；用真的 `tradingagentspro run` 在一个空的临时目录里跑一次，确认生成两个文件、权限 0600、列出九个必填项、退出码 1
