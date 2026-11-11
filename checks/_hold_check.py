"""合约开始前的限制 (MECHANICS 18): 休息 / 外出 / 联网中不能开始行动, 由 app.BreakTimerApp.contract_block 给出提示."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("hold_check")          # 临时文件放在 %TEMP%\OIT 里, 退出时整个删掉 (tempstore.py)

import config
import app


class Net:
    def __init__(self, mode):
        self.mode = mode


class Fake:
    """只带 contract_block 用到的字段, 不建真窗口."""

    def __init__(self, state, mode):
        self.state = state
        self.net = Net(mode)
        self.behavior = None
        self.sync = None


f = app.BreakTimerApp.contract_block
assert f(Fake(config.IDLE, "offline")) == ""
assert f(Fake(config.IDLE, "pending")) == ""
assert f(Fake(config.IDLE, "online")) != ""
assert f(Fake(config.ON_BREAK, "offline")) != ""
assert f(Fake(config.ON_BREAK, "online")) != ""
assert f(Fake(config.RUNNING, "online")) != ""
assert f(Fake(config.IDLE, "dev")) == ""
print("contract block predicate OK")
