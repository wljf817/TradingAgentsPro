"""流程：把多个模块的用例串成流程，比如一个案件从开案到执行的各个阶段（DBOS 工作流），
以及随程序一直运行的盘中巡检这样的进程内循环。

可以依赖 modules（只能通过模块的包根）、contracts 和 shared，不能依赖 integrations 和 bootstrap。
"""
