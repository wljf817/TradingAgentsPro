"""配置文件：主程序的连接信息、密钥和运行参数都在这里，启动时一次读完。

见 settings-file spec 和 add-settings-file 的 design D3、D5、D6。Settings 同时是校验的模式和生成
文件的清单：每个字段是一节，类型是那份配置类。错误信息里只有项的名字，没有值。
"""

import tomllib
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator
from pydantic.fields import FieldInfo
from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

from tradingagentspro.integrations.alpaca import AlpacaBarsConfig, AlpacaConfig, AlpacaNewsConfig
from tradingagentspro.integrations.cboe import CboeConfig
from tradingagentspro.integrations.discord import DiscordConfig
from tradingagentspro.integrations.finnhub import FinnhubConfig
from tradingagentspro.integrations.llm import LlmConfig
from tradingagentspro.integrations.moomoo import MoomooConfig, MoomooSecuritiesConfig
from tradingagentspro.integrations.nyse_calendar import CalendarConfig
from tradingagentspro.integrations.sec import SecConfig
from tradingagentspro.integrations.standin import StandInConfig
from tradingagentspro.modules.companydata import CompanyDataConfig
from tradingagentspro.modules.emergency import EmergencyConfig
from tradingagentspro.modules.events import EventRulesConfig
from tradingagentspro.modules.execution import ExecutionConfig
from tradingagentspro.modules.followups import FollowUpConfig
from tradingagentspro.modules.gateway import GatewayConfig
from tradingagentspro.modules.health import HealthConfig
from tradingagentspro.modules.heartbeat import HeartbeatConfig
from tradingagentspro.modules.marketdata import MarketDataConfig
from tradingagentspro.modules.news import FilingConfig, NewsConfig
from tradingagentspro.modules.notification import NotificationConfig
from tradingagentspro.modules.risk import RiskConfig
from tradingagentspro.modules.scheduler import SchedulerConfig
from tradingagentspro.modules.screener import ScreenerConfig
from tradingagentspro.modules.stockpool import StockPoolConfig
from tradingagentspro.workflows.case_flow import CaseFlowConfig
from tradingagentspro.workflows.close_heartbeat import CloseHeartbeatConfig
from tradingagentspro.workflows.event_detection import EventDetectionConfig
from tradingagentspro.workflows.order_flow import OrderFlowConfig
from tradingagentspro.workflows.patrol import PatrolConfig
from tradingagentspro.workflows.queue_runner import QueueRunnerConfig
from tradingagentspro.workflows.snapshot_flow import SnapshotConfig

SETTINGS_FILE = Path("config/settings.toml")  # 相对工作目录，不进 git

# 状态库只用 PostgreSQL 和 psycopg 3；写 postgresql:// 时 SQLAlchemy 默认找 psycopg2
_DRIVER = "postgresql+psycopg"

# 日线和新闻用 alpaca 一节的 key
_ALPACA_KEYS = ("key_id", "secret_key")
_ALPACA_DATA = ("alpaca_bars", "alpaca_news")

# 不写在这个文件里的项，见 settings-file spec「文件的内容」和 design D5
ELSEWHERE: Mapping[str, tuple[str, ...]] = {
    "alpaca_bars": _ALPACA_KEYS,
    "alpaca_news": _ALPACA_KEYS,
    "execution": ("stagger_buys_and_stops",),
    "gateway": ("general_budget", "reserve_budget", "exploration_budget"),
}


class SettingsError(ValueError):
    """配置文件的内容不对。信息里只有项的名字，没有值。"""


class DatabaseConfig(BaseModel):
    """状态库。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 含用户名和密码：postgresql://用户:密码@主机:端口/数据库，驱动一律用 psycopg
    url: SecretStr

    @field_validator("url")
    @classmethod
    def _use_psycopg(cls, url: SecretStr) -> SecretStr:
        try:
            parsed = make_url(url.get_secret_value())
        except (ArgumentError, ValueError):  # 原来的异常里有地址
            raise ValueError("不是数据库地址") from None
        return SecretStr(parsed.set(drivername=_DRIVER).render_as_string(hide_password=False))

    @property
    def label(self) -> str:
        """写日志用的数据库地址：密码换成 ***。"""
        return make_url(self.url.get_secret_value()).render_as_string(hide_password=True)


class Settings(BaseModel):
    """配置文件的内容，按文件里的顺序：必填项所在的节在前，其余的节写了才生效。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: DatabaseConfig
    alpaca: AlpacaConfig
    discord: DiscordConfig
    finnhub: FinnhubConfig
    sec: SecConfig
    alpaca_bars: AlpacaBarsConfig
    alpaca_news: AlpacaNewsConfig
    news: NewsConfig = NewsConfig()
    filings: FilingConfig = FilingConfig()
    cboe: CboeConfig = CboeConfig()
    moomoo: MoomooConfig = MoomooConfig()
    moomoo_securities: MoomooSecuritiesConfig = MoomooSecuritiesConfig()
    llm: LlmConfig = LlmConfig()
    standin: StandInConfig = StandInConfig()
    calendar: CalendarConfig = CalendarConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()
    emergency: EmergencyConfig = EmergencyConfig()
    health: HealthConfig = HealthConfig()
    marketdata: MarketDataConfig = MarketDataConfig()
    notification: NotificationConfig = NotificationConfig()
    order_flow: OrderFlowConfig = OrderFlowConfig()
    patrol: PatrolConfig = PatrolConfig()
    stockpool: StockPoolConfig = StockPoolConfig()
    followups: FollowUpConfig = FollowUpConfig()
    gateway: GatewayConfig = GatewayConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    heartbeat: HeartbeatConfig = HeartbeatConfig()
    close_heartbeat: CloseHeartbeatConfig = CloseHeartbeatConfig()
    case_flow: CaseFlowConfig = CaseFlowConfig()
    queue_runner: QueueRunnerConfig = QueueRunnerConfig()
    snapshot: SnapshotConfig = SnapshotConfig()
    events: EventRulesConfig = EventRulesConfig()
    event_detection: EventDetectionConfig = EventDetectionConfig()
    companydata: CompanyDataConfig = CompanyDataConfig()
    screener: ScreenerConfig = ScreenerConfig()


def sections() -> Iterator[tuple[str, type[BaseModel]]]:
    """每一节的名字和它的配置类。"""
    for name, field in Settings.model_fields.items():
        yield name, cast(type[BaseModel], field.annotation)


def items(section: str, config: type[BaseModel]) -> Iterator[tuple[str, FieldInfo]]:
    """这一节写在文件里的项。"""
    for name, field in config.model_fields.items():
        if name not in ELSEWHERE.get(section, ()):
            yield name, field


def load_settings(path: Path) -> Settings:
    """读配置文件：先一次列出全部缺少的项，再一次列出全部写错的项，都抛 SettingsError。"""
    file = tomllib.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    missing = _missing(file)
    if missing:
        raise SettingsError(f"{path} 缺少这些项：{'、'.join(missing)}")
    misplaced = _misplaced(file)
    if misplaced:
        raise SettingsError(f"{path} 不能写这些项：{'、'.join(misplaced)}")
    keys = {name: file["alpaca"][name] for name in _ALPACA_KEYS}
    tables = file | {section: file.get(section, {}) | keys for section in _ALPACA_DATA}
    try:
        return Settings.model_validate(tables)
    except ValidationError as error:  # 原来的异常里有输入的值
        raise SettingsError(f"{path} 这些项不对：{_described(error)}") from None


def _missing(file: Mapping[str, Any]) -> list[str]:
    """必填项里没有写或为空的。"""
    return [
        f"{section}.{name}"
        for section, config in sections()
        for name, field in items(section, config)
        if field.is_required() and file.get(section, {}).get(name) in (None, "")
    ]


def _misplaced(file: Mapping[str, Any]) -> list[str]:
    return [
        f"{section}.{name}"
        for section, names in ELSEWHERE.items()
        for name in names
        if name in file.get(section, {})
    ]


def _described(error: ValidationError) -> str:
    """每个错误写成「项：原因」，不取输入的值。"""
    found = error.errors(include_url=False, include_context=False, include_input=False)
    return "；".join(f"{'.'.join(map(str, item['loc']))}：{item['msg']}" for item in found)
