"""板块：11 个 SPDR 板块 ETF，以及数据源的行业到板块的代码表，见 company-data spec「板块」。

行业名按 Finnhub 的 finnhubIndustry 写；一个行业横跨两个板块时按多数归一个，见
add-earnings-calendar-and-sector 的 design D2。
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class SectorEtf(StrEnum):
    """板块：11 个 SPDR 板块 ETF 之一，值就是 ETF 的代码。"""

    XLK = "XLK"  # 科技
    XLC = "XLC"  # 通信服务
    XLY = "XLY"  # 可选消费
    XLP = "XLP"  # 必需消费
    XLV = "XLV"  # 医疗
    XLF = "XLF"  # 金融
    XLE = "XLE"  # 能源
    XLI = "XLI"  # 工业
    XLB = "XLB"  # 材料
    XLU = "XLU"  # 公用事业
    XLRE = "XLRE"  # 房地产


_INDUSTRIES_BY_SECTOR: Final[Mapping[SectorEtf, tuple[str, ...]]] = {
    SectorEtf.XLK: ("Technology", "Semiconductors", "Communications"),
    SectorEtf.XLC: ("Media", "Telecommunication"),
    SectorEtf.XLY: (
        "Retail",
        "Automobiles",
        "Auto Components",
        "Hotels, Restaurants & Leisure",
        "Textiles, Apparel & Luxury Goods",
        "Leisure Products",
        "Diversified Consumer Services",
        "Distributors",
    ),
    SectorEtf.XLP: ("Consumer products", "Beverages", "Food Products", "Tobacco"),
    SectorEtf.XLV: (
        "Pharmaceuticals",
        "Biotechnology",
        "Health Care",
        "Life Sciences Tools & Services",
    ),
    SectorEtf.XLF: ("Banking", "Financial Services", "Insurance"),
    SectorEtf.XLE: ("Energy",),
    SectorEtf.XLI: (
        "Aerospace & Defense",
        "Airlines",
        "Building",
        "Commercial Services & Supplies",
        "Construction",
        "Electrical Equipment",
        "Industrial Conglomerates",
        "Logistics & Transportation",
        "Machinery",
        "Marine",
        "Professional Services",
        "Road & Rail",
        "Trading Companies & Distributors",
        "Transportation Infrastructure",
    ),
    SectorEtf.XLB: ("Chemicals", "Metals & Mining", "Packaging", "Paper & Forest"),
    SectorEtf.XLU: ("Utilities",),
    SectorEtf.XLRE: ("Real Estate",),
}

# 去掉首尾空白、casefold 以后的行业名 → 板块
_SECTORS: Final[Mapping[str, SectorEtf]] = MappingProxyType(
    {
        industry.strip().casefold(): sector
        for sector, industries in _INDUSTRIES_BY_SECTOR.items()
        for industry in industries
    }
)


def sector_of(industry: str) -> SectorEtf | None:
    """行业对应的板块；去掉首尾空白、不分大小写比较，代码表里没有时为 None。"""
    return _SECTORS.get(industry.strip().casefold())
