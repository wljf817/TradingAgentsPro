# Tasks

## 1. 依赖和测试用的 Alpaca

- [ ] 1.1 `uv add httpx`；`pyproject.toml` 的 pytest 配置登记标记 `alpaca_paper`（连真实模拟盘的冒烟测试），`addopts` 加 `-m "not alpaca_paper"`（design D9）。验证：`uv run pytest -q` 全部通过，`uv run pytest --markers` 列出 `alpaca_paper`
- [ ] 1.2 新增 `tests/integrations/alpaca/__init__.py`、`payloads.py`（Alpaca 文档样例里的订单、账户、持仓 JSON，函数按需覆盖字段）和 `server.py`（`FakeAlpaca`，见 design D9：按订单号保存订单，持仓、账户、账户配置，按方法和路径分发 D1 表里的 9 个接口，记录收到的请求，可以安排下一次某个请求返回给定状态码和 message，或者抛 `httpx.ConnectTimeout`、`httpx.ConnectError`）。验证：`uv run mypy`、`uv run ruff check` 通过

## 2. 客户端：配置、失败分类、状态映射和替换

- [ ] 2.1 先写测试 `tests/integrations/alpaca/test_client.py`：
  - 配置（design D2）：默认地址和超时 10 秒；超时 0 和空的 key ID 报错；请求发往配置的地址，带 `APCA-API-KEY-ID`、`APCA-API-SECRET-KEY`
  - 失败分类（design D2 的 `_send`）：`ConnectTimeout`、`ReadTimeout`、`ConnectError` 和 429、500、503 都报 `BrokerUnavailableError`；4xx 原样交给调用方
  - 状态映射（design D3）：spec「订单状态的映射」表里的每一个状态，加上一个表里没有的状态
  - 订单解析：字符串的股数和价格精确转成 Decimal（"49.955"），没有成交均价时为 None
  - 替换（design D4）：没有替换时不多发请求；往后两跳找到现在的那一笔；往前加往后累计成交 40、均价 46.625；pending_replace、还没有 `replaced_by` 时现在的那一笔就是旧单

  验证：确认因为 `tradingagentspro.integrations.alpaca` 不存在而失败
- [ ] 2.2 实现 `src/tradingagentspro/integrations/alpaca/client.py`：`PAPER_URL`、`AlpacaConfig`、`AlpacaOrder`、`AlpacaPosition`、`AlpacaAccount`、`AlpacaClient`（`_send` 和 D1 的各个请求）、状态映射、`Chain` 和沿替换找订单；`__init__.py` 写包说明并导出。验证：2.1 的测试通过；`uv run mypy`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 3. 普通订单的适配器

- [ ] 3.1 先写测试 `tests/integrations/alpaca/test_broker.py`（design D5、D6），每个测试的说明写明对应的 spec 场景：
  - 「订单状态的映射」全部场景，「查询订单」全部场景，「提交当日有效单」全部场景
  - 「撤单」的撤单请求被接受、订单已经结束，以及 Alpaca 查不到订单号时正常返回
  - 「持仓」全部场景，「账户」全部场景，「全部挂单」的普通订单和止损单分开（只看普通订单这一半）
  - 「费用估算」全部场景，「调用失败的分类」全部场景，「Alpaca 的防自成交保护」的持仓挂着止损单时加仓被拒
  - 类型检查：`broker: Broker = AlpacaBroker(...)`

  验证：确认因为 `AlpacaBroker` 不存在而失败
- [ ] 3.2 实现 `integrations/alpaca/broker.py` 的 `AlpacaBroker`，包根导出。验证：3.1 的测试通过；`uv run mypy` 通过

## 4. 止损单的适配器

- [ ] 4.1 先写测试 `tests/integrations/alpaca/test_stops.py`（design D4、D5），每个测试的说明写明对应的 spec 场景：
  - 「挂止损单」「查询止损单」「改单产生的替换」「改单」全部场景
  - 「撤单」的撤止损单，「全部挂单」的普通订单和止损单分开（止损单这一半）和改单还在进行中
  - 「Alpaca 的防自成交保护」的买单还挂着时止损单被拒
  - 止损单连同替换只收一笔佣金（「费用估算」）
  - 类型检查：`stops: StopBroker = AlpacaStopBroker(...)`

  验证：确认因为 `AlpacaStopBroker` 不存在而失败
- [ ] 4.2 实现 `integrations/alpaca/stops.py` 的 `AlpacaStopBroker`，包根导出。验证：4.1 的测试通过；`uv run mypy` 通过

## 5. 账户设置

- [ ] 5.1 先写测试 `tests/integrations/alpaca/test_account_config.py`：spec「账户设置」的两个场景，以及重复设置时请求内容相同、只改 `no_shorting` 和 `max_margin_multiplier` 两项。验证：确认因为 `configure_account` 不存在而失败
- [ ] 5.2 实现 `configure_account`（design D7），包根导出。验证：5.1 的测试通过；`uv run mypy`、`uv run lint-imports` 通过

## 6. 连真实模拟盘的冒烟测试

- [ ] 6.1 写 `tests/integrations/alpaca/test_paper_smoke.py`（design D9），全部加 `alpaca_paper` 标记，没有 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY` 时跳过。客户端订单 ID 用 `secrets.token_hex` 生成（`uuid4` 被 Ruff 禁用），等待用 `threading.Event().wait`（`time` 被禁用）：
  - 任何时候：账户设置后读回配置；读取账户、全部持仓、全部挂单；远低于市价的 1 股限价买单提交、查询、撤单、查到撤销；查询一个不存在的客户端订单 ID 得到查不到
  - 只在开盘时（`GET /v2/clock` 的 `is_open`，否则跳过）：市价买 1 股并等到成交；挂远低于市价的止损单；再提交买单，确认被拒受理（防自成交）；改止损价，确认查询跟着替换找到新单、全部挂着的止损单只有一张；撤止损单；市价卖出这 1 股

  README 的「常用命令」加一行运行方法。验证：没有 key 时 `uv run pytest -m alpaca_paper` 全部跳过，`uv run pytest` 不收集这些测试
- [ ] 6.2 人在环境变量里给出 Alpaca 模拟盘的 key 以后，开盘时运行 `uv run pytest -m alpaca_paper`。验证：全部通过；和 spec 不符的地方先改 spec 和 design，再改代码和 2 到 5 组的测试。没有 key 时这一项留着，在「启动与装配」之前补上

## 7. 收尾

- [ ] 7.1 人同意 proposal「需要人决定」第 1 条以后，按推荐修改完整版 Proposal「技术栈与部署」券商一行，和 `docs/实现路线.md` M7 的完成标志。验证：只改了这两处，和 spec「Alpaca 的防自成交保护」一致
- [ ] 7.2 对照 spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 7.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`，全部通过
