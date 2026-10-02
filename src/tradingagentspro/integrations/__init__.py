"""外部系统的适配器：实现各模块 ports 里定义的接口，比如 moomoo、Discord、交易日历库、系统时钟。

可以依赖 modules（只能通过模块的包根）、contracts 和 shared，不能依赖 workflows 和 bootstrap。
"""
