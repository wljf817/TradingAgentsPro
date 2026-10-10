"""contracts 里的全部 Pydantic 模型：构造以后不能修改，不接受多出来的字段。

逐个模块找出定义在 contracts 里的模型并检查配置，不再给每个模型单独写这两项的测试。
signal-format spec「构造以后不能修改」和 task-dispatch spec「Task 的格式」的"生成以后不能修改"
由这里覆盖。
"""

import importlib
import inspect
import pkgutil

from pydantic import BaseModel

from tradingagentspro import contracts
from tradingagentspro.contracts.trade_plan import TradePlan


def _contract_models() -> list[type[BaseModel]]:
    """定义在 contracts 各模块里的模型，不含从别处 import 进来的。"""
    models: list[type[BaseModel]] = []
    for info in pkgutil.walk_packages(contracts.__path__, f"{contracts.__name__}."):
        module = importlib.import_module(info.name)
        models += [
            value
            for value in vars(module).values()
            if inspect.isclass(value)
            and issubclass(value, BaseModel)
            and value.__module__ == module.__name__
        ]
    return models


def test_every_model_is_frozen_and_forbids_extra_fields() -> None:
    models = _contract_models()
    wrong = [
        f"{model.__module__}.{model.__qualname__}"
        for model in models
        if model.model_config.get("frozen") is not True
        or model.model_config.get("extra") != "forbid"
    ]

    assert TradePlan in models
    assert not wrong, '要设 frozen=True、extra="forbid"：\n' + "\n".join(wrong)
