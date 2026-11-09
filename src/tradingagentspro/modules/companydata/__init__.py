"""公司数据：一只股票属于哪个板块（11 个 SPDR 板块 ETF 之一），以及财报日历（含一致预期和实际值）
和最近几个季度的 EPS surprise。

规则见 openspec 的 company-data spec。数据源和财报的存储由 bootstrap 注入；财报存在状态库里，只由
这个模块写入：财报日历在 companydata_earnings_releases 表，surprise 在
companydata_earnings_surprises 表，数据库实现是 PostgresEarningsStore。

还从 SEC 取财务事实，存在 companydata_financial_facts 表（PostgresFinancialFactStore），按财季
算出 financials 的结果（FinancialsService），见 financials-tool spec。估值用财务事实、原始收盘价和
拆股记录，价格和拆股每次向数据源取，不存，见 valuation-tool spec。模块之外的代码只能 import 这里
导出的名字。
"""

from tradingagentspro.modules.companydata.config import CompanyDataConfig
from tradingagentspro.modules.companydata.domain.earnings import (
    EarningsRelease,
    EarningsSurprise,
    ReleaseBeat,
    ReleaseTime,
    SurpriseHistory,
)
from tradingagentspro.modules.companydata.domain.financials import (
    Figure,
    FinancialFact,
    FinancialReport,
    Item,
    QuarterFigures,
)
from tradingagentspro.modules.companydata.domain.sector import SectorEtf, sector_of
from tradingagentspro.modules.companydata.domain.valuation import (
    Appraisal,
    DailyClose,
    Dated,
    Position,
    Price,
    Split,
    ValuationReport,
)
from tradingagentspro.modules.companydata.ports import (
    CompanyDataSource,
    CompanyDataUnavailableError,
    EarningsStore,
    FinancialFactSource,
    FinancialFactStore,
    PriceSource,
    SplitSource,
)
from tradingagentspro.modules.companydata.repo import (
    PostgresEarningsStore,
    PostgresFinancialFactStore,
)
from tradingagentspro.modules.companydata.service import (
    CompanyDataService,
    FinancialsService,
    ValuationService,
)

__all__ = [
    "Appraisal",
    "CompanyDataConfig",
    "CompanyDataService",
    "CompanyDataSource",
    "CompanyDataUnavailableError",
    "DailyClose",
    "Dated",
    "EarningsRelease",
    "EarningsStore",
    "EarningsSurprise",
    "Figure",
    "FinancialFact",
    "FinancialFactSource",
    "FinancialFactStore",
    "FinancialReport",
    "FinancialsService",
    "Item",
    "Position",
    "PostgresEarningsStore",
    "PostgresFinancialFactStore",
    "Price",
    "PriceSource",
    "QuarterFigures",
    "ReleaseBeat",
    "ReleaseTime",
    "SectorEtf",
    "Split",
    "SplitSource",
    "SurpriseHistory",
    "ValuationReport",
    "ValuationService",
    "sector_of",
]
