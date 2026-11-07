"""比率写成百分数，见 financials-tool spec「代码算的数」「一致预期和实际值」。"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

_PERCENT: Final = Decimal("0.1")


def percent(numerator: Decimal, denominator: Decimal) -> Decimal:
    """numerator 除以 denominator 写成百分数，保留一位小数，四舍五入。"""
    return (numerator / denominator * 100).quantize(_PERCENT, ROUND_HALF_UP)
