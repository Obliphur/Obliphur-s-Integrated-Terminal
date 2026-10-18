import config
import app

class Net:
    def __init__(self, mode): self.mode = mode

class Fake:
    def __init__(self, state, mode):
        self.state = state
        self.net = Net(mode)

f = app.BreakTimerApp._contract_hold
assert f(Fake(config.IDLE, "offline")) is False
assert f(Fake(config.IDLE, "pending")) is False
assert f(Fake(config.IDLE, "online")) is True
assert f(Fake(config.ON_BREAK, "offline")) is True
assert f(Fake(config.ON_BREAK, "online")) is True
assert f(Fake(config.RUNNING, "online")) is True
assert f(Fake(config.IDLE, "dev")) is False
print("contract hold predicate OK")
