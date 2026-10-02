"""业务模块：每个模块一个子包，对应架构图里的一个节点。

模块内部固定由这些文件组成：__init__.py（公开 API）、service.py、repo.py、
ports.py、domain/、config.py。模块之外的代码只能 import 模块的包根。
可以依赖 contracts 和 shared，不能依赖 integrations、workflows 和 bootstrap。
"""
