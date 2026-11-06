"""生成配置文件：从 Settings 的配置项写出 TOML 文本，不另外维护模板或默认值。

见 settings-file spec「生成的文件」「和默认值不同的项写进日志」，以及 add-settings-file 的 design
D3、D4、D8。说明写成 ## 开头，默认值写成 # 开头：去掉行首的 # 才生效。
"""

import inspect
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from tradingagentspro.bootstrap.files import ensure_private_file
from tradingagentspro.bootstrap.settings import Settings, items, sections

_HEADER = """\
## 主程序的配置文件：第一次启动时生成，之后只改这一份，改完重启生效。含义见 settings-file spec。
## 这个文件不进 git，里面有密钥。模型、厂商和预算额度在 config/models.toml。
##
## 值是 "" 的项必须填写，都写在引号里。其余的项去掉行首的 # 才生效，没去掉的用代码里的默认值；
## 注释里的值是生成这个文件时的默认值。比例写小数，时长写秒数。"""

# 配置类里的一个字段，可以带行尾注释
_FIELD = re.compile(r" {4}([a-z_]+): .*?(?:  # (.+))?")


def render_settings() -> str:
    """配置文件的全文。"""
    lines = [_HEADER]
    for section, config in sections():
        notes = _notes(config)
        lines += ["", *(f"## {line}" for line in _title(config)), f"[{section}]"]
        for name, field in items(section, config):
            lines += [f"## {note}" for note in notes.get(name, ())]
            lines += _entry(name, field)
    return "\n".join(lines) + "\n"


def ensure_settings_file(path: Path) -> bool:
    """配置文件不存在时生成；给出这次有没有生成。"""
    return ensure_private_file(path, render_settings())


def overridden(settings: Settings) -> list[str]:
    """和默认值不同的项，写法和文件里一样。必填项没有默认值，不在其中，所以这里没有密钥。"""
    changed = []
    for section, config in sections():
        values = getattr(settings, section)
        for name, field in items(section, config):
            value = getattr(values, name)
            if not field.is_required() and value != field.default:
                changed.append(f"{section}.{name} = {_value(value)}")
    return changed


def _title(config: type[BaseModel]) -> list[str]:
    """docstring 的第一段。"""
    return inspect.cleandoc(config.__doc__ or "").split("\n\n")[0].splitlines()


def _notes(config: type[BaseModel]) -> dict[str, list[str]]:
    """配置类的源码里写在字段上面和行尾的注释。"""
    notes: dict[str, list[str]] = {}
    above: list[str] = []
    for line in inspect.getsource(config).splitlines():
        if line.startswith("    # "):
            above.append(line.removeprefix("    # "))
            continue
        if field := _FIELD.fullmatch(line):
            notes[field[1]] = above if field[2] is None else [*above, field[2]]
        above = []
    return notes


def _entry(name: str, field: FieldInfo) -> list[str]:
    """一项：必填项写成空字符串，其余的写成注释，值是默认值。"""
    if field.is_required():
        return [f'{name} = ""']
    default = field.default
    if isinstance(default, tuple) and default and isinstance(default[0], BaseModel):
        lines = [f"{name} = [", *(f"  {_value(item)}," for item in default), "]"]
    elif isinstance(default, timedelta):
        lines = [f"{name} = {_value(default)}  # 秒"]
    else:
        lines = [f"{name} = {_value(default)}"]
    return [f"# {line}" for line in lines]


def _value(value: object) -> str:
    """一个值的 TOML 写法，写在一行里，见 design D4。"""
    match value:
        case Enum():
            return _value(value.value)
        case bool():
            return str(value).lower()
        case str():
            return json.dumps(value, ensure_ascii=False)  # JSON 字符串是合法的 TOML 字符串
        case int():
            return str(value)
        case Decimal():
            return format(value, "f")
        case timedelta():
            return format(Decimal(value.total_seconds()).normalize(), "f")
        case date():
            return value.isoformat()
        case BaseModel():
            names = type(value).model_fields
            return f"{{ {', '.join(f'{n} = {_value(getattr(value, n))}' for n in names)} }}"
        case frozenset():
            return _value(sorted(value))
        case tuple() | list():
            return f"[{', '.join(_value(item) for item in value)}]"
    raise TypeError(type(value))
