"""全部测试共用的设置：时区守卫和 Hypothesis 配置。"""

import os
import time

from hypothesis import settings

# 时区守卫：测试进程固定在 UTC+14 运行。美股常规交易时段对应的 UTC 时间换到 UTC+14
# 都已经是第二天，偷偷依赖本机时区的代码在测试里会算错日期。
os.environ["TZ"] = "Pacific/Kiritimati"
time.tzset()

# dev：本地随机探索，失败样例存进 .hypothesis/。
# ci：固定随机种子，同一个提交在 CI 里结果可重现；失败时打印复现所需的数据。
# 纯逻辑不测耗时，两套都关掉 deadline，避免慢机器上的偶发失败。
settings.register_profile("dev", deadline=None)
settings.register_profile(
    "ci",
    deadline=None,
    derandomize=True,
    print_blob=True,
    max_examples=200,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
