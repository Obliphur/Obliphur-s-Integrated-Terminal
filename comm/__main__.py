import os

import comm

# 自检在 comm/comm_selftest.py: 用 comm 的全局命名空间执行, 所以它读的常量 /
# 改写 FOLLOW_DELAY / CHAR_DELAY / CLAUDIA_LEGACIES 等, 和以前写在同一份文件里时一样.
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comm_selftest.py")
with open(path, encoding="utf-8") as f:
    exec(compile(f.read(), path, "exec"), comm.__dict__)
print(comm._selftest())
