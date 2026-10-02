"""纽交所日历：用 exchange_calendars 读取 XNYS 的交易时段，构造交易日历。"""

from datetime import UTC, date, timedelta

import exchange_calendars

from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession

# 默认加载范围：起点固定；终点是启动时刻往后 DEFAULT_LOOKAHEAD，远超最长的前瞻（60 个交易日）。
DEFAULT_FIRST_DAY = date(2020, 1, 1)
DEFAULT_LOOKAHEAD = timedelta(days=730)


def load_nyse_calendar(start: date, end: date) -> TradingCalendar:
    """加载 start 到 end（含两端）的纽交所交易时段。纳斯达克上市的股票用同一套交易日和常规时段。

    起止日期一律显式传入：不传时，日历库会按本机的当前时间计算默认范围。
    """
    xnys = exchange_calendars.get_calendar("XNYS", start=start.isoformat(), end=end.isoformat())
    # 日历库给出的时区对象是 ZoneInfo("UTC")，统一换成 datetime.UTC。
    return TradingCalendar(
        TradingSession(
            day=label.date(),
            open=row["open"].to_pydatetime().astimezone(UTC),
            close=row["close"].to_pydatetime().astimezone(UTC),
        )
        for label, row in xnys.schedule.iterrows()
    )
