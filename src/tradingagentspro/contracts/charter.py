"""系统宪章：bootstrap 读入，真实模型的后端交给 PM，案件工作流把版本写进程序日志。

见 shared-context spec「系统宪章」。
"""

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class Charter(BaseModel):
    """一版系统宪章。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    version: PositiveInt
    text: str = Field(min_length=1)
