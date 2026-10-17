"""公司数据：一只股票属于哪个板块（11 个 SPDR 板块 ETF 之一），以及财报日历（含一致预期和实际值）
和最近几个季度的 EPS surprise。

规则见 openspec 的 company-data spec。数据源和财报的存储由 bootstrap 注入；财报存在状态库里，只由
这个模块写入：财报日历在 companydata_earnings_releases 表，surprise 在
companydata_earnings_surprises 表，数据库实现是 PostgresEarningsStore。模块之外的代码只能 import
这里导出的名字。
"""

from tradingagentspro.modules.companydata.config import CompanyDataConfig
from tradingagentspro.modules.companydata.domain.earnings import (
    EarningsRelease,
    EarningsSurprise,
    ReleaseTime,
    SurpriseHistory,
)
from tradingagentspro.modules.companydata.domain.sector import SectorEtf, sector_of
from tradingagentspro.modules.companydata.ports import (
    CompanyDataSource,
    CompanyDataUnavailableError,
    EarningsStore,
)
from tradingagentspro.modules.companydata.repo import PostgresEarningsStore
from tradingagentspro.modules.companydata.service import CompanyDataService

__all__ = [
    "CompanyDataConfig",
    "CompanyDataService",
    "CompanyDataSource",
    "CompanyDataUnavailableError",
    "EarningsRelease",
    "EarningsStore",
    "EarningsSurprise",
    "PostgresEarningsStore",
    "ReleaseTime",
    "SectorEtf",
    "SurpriseHistory",
    "sector_of",
]
